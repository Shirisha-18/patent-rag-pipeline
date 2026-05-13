"""
date_parser.py
--------
Era-aware USPTO patent date extractor.

WHAT CHANGED FROM date4.py
===========================
1.  Era boundaries updated to match TIFF-observed layout changes:
        A   1         –  137,278   (1836–1872)
        B   137,279   –  795,260   (1873–1905)
        C   795,261   – 1,427,765  (1906–1930)
        D  1,427,766  – 2,692,986  (1930–1960)
        E  2,692,987  – 3,579,736  (1960–1971)
        F  3,579,737  +             (1971–now)

2.  B and C share one extractor (same anchors, same keywords — only
    position of issue date differs: header in B, body in C).
    D is now its own extractor because its filing keyword changed
    ("Application [date]" with no "filed" word).

3.  Confidence tags are now mechanistic, not qualitative:
        era_primary     — found by the pattern native to this patent's era
        regex_A … regex_E — found by a different era's fallback pattern
        body_scan       — found by generic FLEXIBLE_DATE sweep
        universal_sweep — found by last-resort dateparser line sweep
        MISSING         — structurally absent (Era A filing)
        NONE            — all passes failed; needs manual review

4.  ExtractionResult gains two new fields:
        issue_pass   : str   — which pass/regex found the issue date
        filing_pass  : str   — which pass/regex found the filing date
    These flow into run.py's output CSV so you can track extraction
    health at scale (if body_scan fires >5 % for one era, that era's
    primary pattern needs tuning).

6-PASS ARCHITECTURE (per date, per patent)
===========================================
    Pass 1  — era_classifier() — routes to primary extractor
    Pass 2  — era-native primary extractor          → tag: era_primary
    Pass 3  — adjacent-era fallbacks (±1, then ±2)  → tag: regex_X
    Pass 4  — all remaining era patterns             → tag: regex_X
    Pass 5  — generic FLEXIBLE_DATE body sweep       → tag: body_scan
    Pass 6  — universal dateparser line sweep        → tag: universal_sweep
    NONE    — all passes failed

Pass 6 sanity gate: year must be in [EARLIEST_YEAR, LATEST_YEAR],
month 1–12, day 1–31.  Without this, serial numbers parse as dates.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from dateparser import parse as dateparse


# =============================================================================
# CONSTANTS
# =============================================================================

# Era boundaries — from TIFF image observation, not OCR assumptions
ERA_A_END = 137_278
ERA_B_END = 795_260
ERA_C_END = 1_427_765
ERA_D_END = 2_692_986
ERA_E_END = 3_579_736
# ERA_F = everything above ERA_E_END

# Year sanity gate for Pass 6
EARLIEST_YEAR = 1836  # first modern USPTO patent
LATEST_YEAR = 2010  # upper bound for this dataset

# Generic date regex — matches any "Month DD, YYYY" or "Mon. DD YYYY" form
FLEXIBLE_DATE = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"

# Numeric date regex — MM/DD/YYYY or MM-DD-YYYY (for Pass 6 only)
NUMERIC_DATE = r"\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})\b"

# INID priority codes — lines with these introduce blocks to suppress
PRIORITY_CODES = frozenset(
    ["[30]", "[32]", "[62]", "[63]", "(30)", "(32)", "(62)", "(63)"]
)

# OCR-robust "filed" — correct + common OCR corruptions
_FILED_PAT = r"(?:filed?|fled|f1led|filled)"

# Dateparser settings — lock date order to MDY to prevent locale drift
_DP_SETTINGS = {
    "DATE_ORDER": "MDY",
    "PREFER_DAY_OF_MONTH": "first",
    "RETURN_AS_TIMEZONE_AWARE": False,
}

# Adjacency order for Pass 3/4 fallbacks — keyed by era
_ERA_ORDER = ["A", "B", "C", "D", "E", "F"]


# =============================================================================
# OUTPUT CONTRACT
# =============================================================================

# Confidence/pass tags — mechanistic, not qualitative
PASS_ERA_PRIMARY = "era_primary"
PASS_REGEX_A = "regex_A"
PASS_REGEX_B = "regex_B"
PASS_REGEX_C = "regex_C"
PASS_REGEX_D = "regex_D"
PASS_REGEX_E = "regex_E"
PASS_BODY_SCAN = "body_scan"
PASS_UNIVERSAL = "universal_sweep"
PASS_MISSING = "MISSING"
PASS_NONE = "NONE"

# Map era tag → regex_X pass tag for use in fallback loops
_ERA_PASS_TAG = {
    "A": PASS_REGEX_A,
    "B": PASS_REGEX_B,
    "C": PASS_REGEX_C,
    "D": PASS_REGEX_D,
    "E": PASS_REGEX_E,
    "F": PASS_REGEX_E,  # F uses E's regex family in fallback
}


@dataclass
class ExtractionResult:
    issue_date: Optional[str]  # MM/DD/YYYY or None
    filing_date: Optional[str]  # MM/DD/YYYY or None
    issue_pass: str  # which pass found the issue date
    filing_pass: str  # which pass found the filing date
    era: str  # "A" / "B" / "C" / "D" / "E" / "F"

    def to_parts(self):
        """Return (iyear, imonth, iday, fyear, fmonth, fday) strings."""

        def split(d):
            if not d:
                return "", "", ""
            try:
                dt = datetime.strptime(d, "%m/%d/%Y")
                return str(dt.year), str(dt.month), str(dt.day)
            except ValueError:
                return "", "", ""

        iy, im, id_ = split(self.issue_date)
        fy, fm, fd = split(self.filing_date)
        return iy, im, id_, fy, fm, fd


# =============================================================================
# SHARED UTILITIES
# =============================================================================


def normalize_text(text: str) -> str:
    """Strip combining diacritics introduced by OCR."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def dehyphenate(text: str) -> str:
    """
    Collapse OCR line-break hyphens: 'applica-\\ntion' → 'application'.
    Handles hard hyphen and soft hyphen (U+00AD).
    """
    return re.sub(r"[-\u00ad]\s*\n\s*", "", text)


def parse_date(raw: str) -> Optional[str]:
    """
    Parse a raw date string → MM/DD/YYYY, or None.
    Settings lock DATE_ORDER=MDY to prevent locale drift across machines.
    """
    dt = dateparse(raw, settings=_DP_SETTINGS)
    if dt:
        return dt.strftime("%m/%d/%Y")
    return None


def first_date_in_line(line: str) -> Optional[str]:
    """Return the first FLEXIBLE_DATE match in a line, parsed to MM/DD/YYYY."""
    m = re.search(FLEXIBLE_DATE, line, re.I)
    if m:
        return parse_date(m.group(1))
    return None


def lines_of(text: str) -> list:
    """Return non-empty stripped lines."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _to_dt(date_str: Optional[str]) -> Optional[datetime]:
    """MM/DD/YYYY string → datetime, or None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%m/%d/%Y")
    except ValueError:
        return None


def _valid_for_gate(date_str: Optional[str]) -> bool:
    """
    Pass 6 sanity gate.
    Returns True only if year is in [EARLIEST_YEAR, LATEST_YEAR].
    """
    dt = _to_dt(date_str)
    if dt is None:
        return False
    return EARLIEST_YEAR <= dt.year <= LATEST_YEAR


def _sliding_joins(line_list: list) -> list:
    """
    Yield single lines plus 2-line and 3-line joins.
    Handles OCR splits without global combinatorial explosion.
    """
    result = []
    n = len(line_list)
    for i in range(n):
        result.append(line_list[i])
        if i + 1 < n:
            result.append(line_list[i] + " " + line_list[i + 1])
        if i + 2 < n:
            result.append(
                line_list[i] + " " + line_list[i + 1] + " " + line_list[i + 2]
            )
    return result


# =============================================================================
# ERA CLASSIFIER
# =============================================================================


def era_classifier(patent_num: int, text: str) -> str:
    """
    Return era tag: "A" | "B" | "C" | "D" | "E" | "F"

    For the E→F transition we sniff the text for INID codes rather than
    relying on the number alone, because some late-E patents adopted the
    INID format before the official boundary.
    """
    if patent_num <= ERA_A_END:
        return "A"
    if patent_num <= ERA_B_END:
        return "B"
    if patent_num <= ERA_C_END:
        return "C"
    if patent_num <= ERA_D_END:
        return "D"
    if patent_num <= ERA_E_END:
        return "F" if _has_inid_codes(text) else "E"
    return "F" if _has_inid_codes(text) else "E"


def _has_inid_codes(text: str) -> bool:
    """True if the document contains both [22] and [45] INID anchors."""
    return bool(re.search(r"\[22\]", text) and re.search(r"\[45\]", text))


# =============================================================================
# PRIMARY EXTRACTORS  (Pass 2 — era-native patterns)
# =============================================================================


def _era_a_issue(all_lines: list) -> Optional[str]:
    """
    Era A issue: 'Letters Patent No. XXX, dated Month DD, YYYY'
    Searches first 30 lines only.
    """
    pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"
    for line in _sliding_joins(all_lines[:30]):
        m = re.search(pat, line)
        if m:
            return parse_date(m.group(1))
    return None


def _era_bc_issue(all_lines: list) -> Optional[str]:
    """
    Era B/C issue: 'Patented Month DD, YYYY'
    B: appears in header (first 10 lines).
    C: appears in body (searched up to line 80).
    We search the full range — primary extractor returns first match.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in _sliding_joins(all_lines[:80]):
        m = re.search(pat, line)
        if m:
            return parse_date(m.group(1))
    return None


def _era_bc_filing(all_lines: list) -> Optional[str]:
    """
    Era B/C filing: 'Application filed Month DD, YYYY'
    Stops before 'Renewed' — that date is not the filing date.
    Accepts OCR corruptions of 'filed' via _FILED_PAT.
    """
    pat = rf"[Aa]pplication\s+{_FILED_PAT}\s+{FLEXIBLE_DATE}"
    for line in _sliding_joins(all_lines[:50]):
        clean = re.split(r"\bRenewed\b", line, flags=re.I)[0]
        m = re.search(pat, clean)
        if m:
            return parse_date(m.group(1))
    return None


def _era_d_issue(all_lines: list) -> Optional[str]:
    """
    Era D issue: 'Patented Month DD, YYYY' — top line, paired with patent number.
    Searches first 50 lines with sliding joins.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in _sliding_joins(all_lines[:50]):
        m = re.search(pat, line)
        if m:
            return parse_date(m.group(1))
    return None


def _era_d_filing(all_lines: list) -> Optional[str]:
    """
    Era D filing: 'Application Month DD, YYYY, Serial No.'
    NO 'filed' keyword — this is what distinguishes D from B/C.
    Handles divided applications: prefers 'this application' date.
    """
    filing_primary = rf"\bApplication\s+{FLEXIBLE_DATE}"
    filing_divided = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    original_pat = r"[Oo]riginal\s+application"

    for line in _sliding_joins(all_lines[:50]):
        m = re.search(filing_divided, line, re.I)
        if m:
            return parse_date(m.group(1))

    for line in _sliding_joins(all_lines[:50]):
        if re.search(original_pat, line, re.I):
            continue
        m = re.search(filing_primary, line, re.I)
        if m:
            return parse_date(m.group(1))
    return None


def _era_e_filing(all_lines: list) -> Optional[str]:
    """
    Era E filing: 'Filed Month DD, YYYY, Ser. No.' — distinct 'Filed' keyword.
    Handles divided applications.
    """
    filing_e = rf"\bFiled\s+{FLEXIBLE_DATE}"
    filing_div = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    original = r"[Oo]riginal\s+application"

    header = _sliding_joins(all_lines[:30])
    for line in header:
        m = re.search(filing_div, line, re.I)
        if m:
            return parse_date(m.group(1))

    for line in header:
        if re.search(original, line, re.I):
            continue
        m = re.search(filing_e, line, re.I)
        if m:
            return parse_date(m.group(1))
    return None


def _era_e_issue(all_lines: list, skip_date: Optional[str] = None) -> Optional[str]:
    """
    Era E issue: 'Patented Month DD, YYYY' — anywhere in body.
    Scans from line 0 (Fix 4: one-column format may have it very early).
    Skips skip_date to avoid returning the filing date as issue.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in all_lines:
        m = re.search(pat, line)
        if m:
            candidate = parse_date(m.group(1))
            if candidate and candidate != skip_date:
                return candidate
    return None


def _era_f_extract(all_lines: list) -> tuple:
    """
    Era F: INID codes [45] (issue) and [22] (filing).

    Returns (issue, filing) both as MM/DD/YYYY or None.

    Fixes carried forward from date4:
        Fix 6a — date-before-anchor: search from anchor_idx - 1
        Fix 6b — priority bleed: suppresses date+country follow-on lines
        Fix 1  — issue == filing: try next candidates
        Fix 2  — priority block persists across follow-on lines
        Fix 3  — [45] before [22] in OCR: swap assignments back
    """
    HEADER_LINES = 80

    PRIORITY_START = re.compile(r"\[3[02]\]|\(3[02]\)|\[6[23]\]|\(6[23]\)", re.I)
    _fd = FLEXIBLE_DATE[1:-1]
    PRIORITY_FOLLOWON = re.compile(
        rf"^(?:"
        rf"[A-Za-z]{{2,30}}"
        rf"|[\d,/\.\-]+"
        rf"|{_fd}"
        rf"|{_fd}\s+[A-Za-z]{{2,30}}"
        rf"|[A-Za-z]{{2,30}}\s+{_fd}"
        rf")\s*$",
        re.I,
    )

    def is_priority_line(line: str, in_block: bool) -> tuple:
        if PRIORITY_START.search(line):
            return True, True
        if in_block:
            if PRIORITY_FOLLOWON.match(line):
                return True, True
            return False, False
        return False, False

    header = all_lines[:HEADER_LINES]

    # Find anchor indices
    idx_22, idx_45, in_prio = None, None, False
    for i, line in enumerate(header):
        skip, in_prio = is_priority_line(line, in_prio)
        if skip:
            continue
        if idx_22 is None and ("[22]" in line or "(22)" in line):
            idx_22 = i
        if idx_45 is None and ("[45]" in line or "(45)" in line):
            idx_45 = i

    # Collect dated lines excluding priority blocks
    dated_lines = []
    in_prio = False
    for i, line in enumerate(header):
        skip, in_prio = is_priority_line(line, in_prio)
        if skip:
            continue
        d = first_date_in_line(line)
        if d:
            dated_lines.append((i, d))

    def dates_from(anchor_idx: Optional[int]) -> list:
        if anchor_idx is None:
            return []
        search_from = max(0, anchor_idx - 1)
        return [date for line_idx, date in dated_lines if line_idx >= search_from]

    filing_candidates = dates_from(idx_22)
    issue_candidates = dates_from(idx_45)

    filing = filing_candidates[0] if filing_candidates else None
    issue = issue_candidates[0] if issue_candidates else None

    # Fix 1: same date for both — try alternates
    if issue and filing and issue == filing:
        alt_issue = issue_candidates[1] if len(issue_candidates) > 1 else None
        alt_filing = filing_candidates[1] if len(filing_candidates) > 1 else None
        if alt_issue and alt_issue != filing:
            issue = alt_issue
        elif alt_filing and alt_filing != issue:
            filing = alt_filing

    # Fix 3: column-reversal
    if issue and filing and idx_22 is not None and idx_45 is not None:
        if idx_45 < idx_22:
            issue, filing = filing, issue

    # Chronological sanity: filing must precede issue
    dt_issue = _to_dt(issue)
    dt_filing = _to_dt(filing)
    if dt_issue and dt_filing and dt_filing > dt_issue:
        issue, filing = filing, issue

    return issue, filing


# =============================================================================
# PASS 5 — GENERIC BODY SCAN
# =============================================================================


def _body_scan(all_lines: list, skip_date: Optional[str] = None) -> Optional[str]:
    """
    Pass 5: scan every line for 'Patented Month DD, YYYY'.
    Returns the first hit that is not skip_date.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in all_lines:
        m = re.search(pat, line)
        if m:
            candidate = parse_date(m.group(1))
            if candidate and candidate != skip_date:
                return candidate
    return None


def _body_scan_filing(
    all_lines: list, skip_date: Optional[str] = None
) -> Optional[str]:
    """
    Pass 5 for filing: scan for any Application/Filed anchor across all lines.
    """
    pats = [
        rf"[Aa]pplication\s+{_FILED_PAT}\s+{FLEXIBLE_DATE}",
        rf"\bFiled\s+{FLEXIBLE_DATE}",
        rf"\bApplication\s+{FLEXIBLE_DATE}",
    ]
    for line in _sliding_joins(all_lines):
        for pat in pats:
            m = re.search(pat, line, re.I)
            if m:
                candidate = parse_date(m.group(1))
                if candidate and candidate != skip_date:
                    return candidate
    return None


# =============================================================================
# PASS 6 — UNIVERSAL SWEEP
# =============================================================================


def _universal_sweep(all_lines: list, skip_date: Optional[str] = None) -> Optional[str]:
    """
    Pass 6: run dateparser on every line in the document.
    Also tries numeric formats (MM/DD/YYYY, MM-DD-YYYY).

    Sanity gate: year must be in [EARLIEST_YEAR, LATEST_YEAR].
    Without this gate, serial numbers like 712,227 parse as dates.

    Returns first valid gated result that is not skip_date, or None.
    """
    # Try FLEXIBLE_DATE first (more specific)
    for line in all_lines:
        for pat in (FLEXIBLE_DATE, NUMERIC_DATE):
            m = re.search(pat, line, re.I)
            if m:
                candidate = parse_date(m.group(1))
                if candidate and candidate != skip_date and _valid_for_gate(candidate):
                    return candidate

    # Try whole-line dateparse as last resort
    for line in all_lines:
        if len(line) > 60:  # skip long lines — too much noise
            continue
        candidate = parse_date(line)
        if candidate and candidate != skip_date and _valid_for_gate(candidate):
            return candidate

    return None


# =============================================================================
# CROSS-ERA FALLBACK EXTRACTORS  (Pass 3 / Pass 4)
# =============================================================================


def _try_era_issue(
    era: str, all_lines: list, skip_date: Optional[str] = None
) -> Optional[str]:
    """
    Attempt to extract an issue date using the primary pattern of `era`.
    Used in Pass 3/4 for cross-era fallbacks.
    """
    if era == "A":
        return _era_a_issue(all_lines)
    if era in ("B", "C"):
        return _era_bc_issue(all_lines)
    if era == "D":
        return _era_d_issue(all_lines)
    if era == "E":
        return _era_e_issue(all_lines, skip_date=skip_date)
    if era == "F":
        issue, _ = _era_f_extract(all_lines)
        return issue
    return None


def _try_era_filing(
    era: str, all_lines: list, skip_date: Optional[str] = None
) -> Optional[str]:
    """
    Attempt to extract a filing date using the primary pattern of `era`.
    Used in Pass 3/4 for cross-era fallbacks.
    """
    if era == "A":
        return None  # Era A never had filing dates
    if era in ("B", "C"):
        return _era_bc_filing(all_lines)
    if era == "D":
        return _era_d_filing(all_lines)
    if era == "E":
        return _era_e_filing(all_lines)
    if era == "F":
        _, filing = _era_f_extract(all_lines)
        return filing
    return None


def _adjacent_era_order(current_era: str) -> list:
    """
    Return other eras sorted by proximity to current_era.
    E.g. for D: [C, E, B, F, A]
    """
    if current_era not in _ERA_ORDER:
        return [e for e in _ERA_ORDER]
    idx = _ERA_ORDER.index(current_era)
    result = []
    for step in range(1, len(_ERA_ORDER)):
        lo = idx - step
        hi = idx + step
        if lo >= 0 and _ERA_ORDER[lo] not in result:
            result.append(_ERA_ORDER[lo])
        if hi < len(_ERA_ORDER) and _ERA_ORDER[hi] not in result:
            result.append(_ERA_ORDER[hi])
    return result


# =============================================================================
# FULL 6-PASS ENGINE
# =============================================================================


def _run_all_passes(
    era: str,
    all_lines: list,
    filing_is_missing: bool = False,
) -> tuple:
    """
    Run all 6 passes for both issue and filing dates.

    Returns:
        (issue_date, issue_pass, filing_date, filing_pass)

    filing_is_missing: if True (Era A), skip all filing passes and
                       return (MISSING, MISSING) immediately for filing.
    """

    issue_date = None
    filing_date = None
    issue_pass = PASS_NONE
    filing_pass = PASS_MISSING if filing_is_missing else PASS_NONE

    # ----------------------------------------------------------------
    # PASS 2 — era-native primary extractor
    # ----------------------------------------------------------------
    if era == "A":
        issue_date = _era_a_issue(all_lines)
    elif era in ("B", "C"):
        issue_date = _era_bc_issue(all_lines)
        if not filing_is_missing:
            filing_date = _era_bc_filing(all_lines)
    elif era == "D":
        issue_date = _era_d_issue(all_lines)
        if not filing_is_missing:
            filing_date = _era_d_filing(all_lines)
    elif era == "E":
        filing_date = _era_e_filing(all_lines)
        issue_date = _era_e_issue(all_lines, skip_date=filing_date)
    elif era == "F":
        issue_date, filing_date = _era_f_extract(all_lines)

    if issue_date:
        issue_pass = PASS_ERA_PRIMARY
    if filing_date and not filing_is_missing:
        filing_pass = PASS_ERA_PRIMARY

    # ----------------------------------------------------------------
    # PASSES 3 + 4 — cross-era fallbacks (adjacent first, then all)
    # ----------------------------------------------------------------
    fallback_order = _adjacent_era_order(era)

    for fallback_era in fallback_order:
        if issue_date and (filing_date or filing_is_missing):
            break  # both found — stop

        tag = _ERA_PASS_TAG.get(fallback_era, PASS_REGEX_E)

        if not issue_date:
            candidate = _try_era_issue(fallback_era, all_lines, skip_date=filing_date)
            if candidate:
                issue_date = candidate
                issue_pass = tag

        if not filing_date and not filing_is_missing:
            candidate = _try_era_filing(fallback_era, all_lines, skip_date=issue_date)
            if candidate:
                filing_date = candidate
                filing_pass = tag

    # ----------------------------------------------------------------
    # PASS 5 — generic body scan
    # ----------------------------------------------------------------
    if not issue_date:
        candidate = _body_scan(all_lines, skip_date=filing_date)
        if candidate:
            issue_date = candidate
            issue_pass = PASS_BODY_SCAN

    if not filing_date and not filing_is_missing:
        candidate = _body_scan_filing(all_lines, skip_date=issue_date)
        if candidate:
            filing_date = candidate
            filing_pass = PASS_BODY_SCAN

    # ----------------------------------------------------------------
    # PASS 6 — universal sweep (dateparser on every line)
    # ----------------------------------------------------------------
    if not issue_date:
        candidate = _universal_sweep(all_lines, skip_date=filing_date)
        if candidate:
            issue_date = candidate
            issue_pass = PASS_UNIVERSAL

    if not filing_date and not filing_is_missing:
        candidate = _universal_sweep(all_lines, skip_date=issue_date)
        if candidate:
            filing_date = candidate
            filing_pass = PASS_UNIVERSAL

    # ----------------------------------------------------------------
    # Final chronological sanity: filing must not be after issue
    # ----------------------------------------------------------------
    dt_i = _to_dt(issue_date)
    dt_f = _to_dt(filing_date)
    if dt_i and dt_f and dt_f > dt_i:
        # Swap dates AND their pass tags
        issue_date, filing_date = filing_date, issue_date
        issue_pass, filing_pass = filing_pass, issue_pass

    return issue_date, issue_pass, filing_date, filing_pass


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================


def extract_dates(text: str, patent_num: int) -> ExtractionResult:
    """
    Main entry point.

    Args:
        text:       Raw OCR text of the patent document.
        patent_num: Integer patent number (no leading zeros).

    Returns:
        ExtractionResult with dates, pass tags, and era label.
    """
    text = normalize_text(text)
    text = dehyphenate(text)
    all_lines = lines_of(text)
    era = era_classifier(patent_num, text)

    filing_is_missing = era == "A"

    issue_date, issue_pass, filing_date, filing_pass = _run_all_passes(
        era=era,
        all_lines=all_lines,
        filing_is_missing=filing_is_missing,
    )

    return ExtractionResult(
        issue_date=issue_date,
        filing_date=filing_date,
        issue_pass=issue_pass,
        filing_pass=filing_pass,
        era=era,
    )

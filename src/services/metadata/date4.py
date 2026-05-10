"""
date_parser.py
--------------
Era-aware USPTO patent date extractor.

Architecture:
    1. era_classifier()     — routes patent to correct extractor by number + text
    2. extract_*()          — one function per era, explicit anchors only
    3. ExtractionResult     — standard output contract for all extractors
    4. extract_dates()      — public entry point

Era boundaries (patent number → year):
    A   1         –  137,279   (1836–1872)  "dated" in spec line, no filing date
    B   137,280   –  589,999   (1873–1897)  "dated" + "Application filed" same line
    B→C 590,000   –  935,999   (1897–1909)  both "Patented" header + "dated" spec line
    C   936,000   – 1,920,165  (1909–1933)  "Patented" own line + "Application filed"
    D  1,920,166  – 2,924,999  (1933–1960)  "Patented" own line + "Application" no filed
    E  2,925,000  – 3,625,113  (1960–1971)  "Filed" header + "Patented" in body
    F  3,625,114  +             (1971–now)   INID codes [22]/[45]

Extraction notes:
    B / BC / C share identical filing logic ("Application filed …") and differ
    only in which issue anchor is present.  They are handled by a single merged
    extractor _extract_era_bcc() that tries both anchors and reconciles them.
    The era tag in ExtractionResult still reflects the number-based bucket
    ("B", "BC", or "C") for reporting continuity.

Fix log:
    Fix 1  — Era F issue=file: try next candidate date before accepting as equal
    Fix 2  — Era F priority bleed: flag persists across bare-date/country follow-on lines
    Fix 3  — Era F [45] before [22] in OCR: detect column reversal, swap assignments
    Fix 4  — Era E NONE issue: body scan starts at line 0; deduplication vs filing date
    Fix 5  — Era D hyphenated line-break: dehyphenate() applied globally before splitting
    Fix 6a — Era F date-before-anchor: dates_from() searches from anchor_idx-1 to catch
             OCR splits where the date value lands on the line above the INID code
    Fix 6b — Era F PRIORITY_FOLLOWON: now also matches "date + country" combo lines
             so "Feb. 12, 1970 France" style priority lines are properly suppressed
    Fix 7  — Era B/BC/C OCR-corrupted "filed": filing pattern accepts common OCR
             misreads: fled / led / filled / f1led in addition to correct "filed"
    Fix 8  — Era B/BC/C merged extractor: single _extract_era_bcc() tries both issue
             anchors ("Patented" and "Letters Patent … dated") for every patent in the
             range; resolves disagreements by preferring "Patented"; marks anchor
             disagreement as LOW confidence so boundary patents are flagged.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from dateparser import parse as dateparse


# =============================================================================
# CONSTANTS
# =============================================================================

ERA_A_END = 137_279
ERA_B_END = 589_999
ERA_BC_END = 935_999
ERA_C_END = 1_920_165
ERA_D_END = 2_924_999
ERA_E_END = 3_625_113
# ERA_F = everything above ERA_E_END

FLEXIBLE_DATE = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"

# INID codes that introduce priority / related-application blocks.
# Lines carrying these codes AND the bare-data lines that follow them
# (dates, country names, application numbers) must all be suppressed.
PRIORITY_CODES = frozenset(
    ["[30]", "[32]", "[62]", "[63]", "(30)", "(32)", "(62)", "(63)"]
)

# Fix 7: OCR-robust "filed" pattern.
# Matches correct spelling plus common OCR corruptions:
#   fled  (i dropped)
#   led   (fi dropped)
#   filled (i doubled)
#   f1led (1 substituted for i)
# Used in Era B, BC, C filing patterns.
_FILED_PAT = r"(?:filed?|fled|f1led|filled)"


# =============================================================================
# OUTPUT CONTRACT
# =============================================================================


class Confidence(str, Enum):
    HIGH = "HIGH"  # two anchors found and agree, or single unambiguous anchor
    MED = "MED"  # one anchor found via primary pattern
    LOW = "LOW"  # found via body scan / fallback
    MISSING = "MISSING"  # structurally absent (e.g. Era A filing date)
    NONE = "NONE"  # not found, needs review


@dataclass
class ExtractionResult:
    issue_date: Optional[str]  # MM/DD/YYYY or None
    filing_date: Optional[str]  # MM/DD/YYYY or None
    issue_confidence: Confidence
    filing_confidence: Confidence
    era: str  # "A" / "B" / "BC" / "C" / "D" / "E" / "F"

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
    Handles both hard hyphen and soft hyphen (U+00AD).
    Only collapses when the hyphen is at end-of-line.
    """
    return re.sub(r"[-\u00ad]\s*\n\s*", "", text)


def parse_date(raw: str) -> Optional[str]:
    """Parse a raw date string → MM/DD/YYYY, or None if unparseable."""
    dt = dateparse(raw)
    if dt:
        return dt.strftime("%m/%d/%Y")
    return None


def first_date_in_line(line: str) -> Optional[str]:
    """Return the first FLEXIBLE_DATE match in a line, parsed to MM/DD/YYYY."""
    m = re.search(FLEXIBLE_DATE, line, re.I)
    if m:
        return parse_date(m.group(1))
    return None


def lines(text: str):
    """Return non-empty stripped lines."""
    return [l.strip() for l in text.splitlines() if l.strip()]


def _to_dt(date_str: Optional[str]) -> Optional[datetime]:
    """MM/DD/YYYY string → datetime, or None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%m/%d/%Y")
    except ValueError:
        return None


# =============================================================================
# ERA CLASSIFIER
# =============================================================================


def era_classifier(patent_num: int, text: str) -> str:
    """
    Return era tag: "A" | "B" | "BC" | "C" | "D" | "E" | "F"

    For the E→F transition zone we sniff the text for INID codes rather
    than relying on number alone.
    """
    if patent_num <= ERA_A_END:
        return "A"
    if patent_num <= ERA_B_END:
        return "B"
    if patent_num <= ERA_BC_END:
        return "BC"
    if patent_num <= ERA_C_END:
        return "C"
    if patent_num <= ERA_D_END:
        return "D"
    if patent_num <= ERA_E_END:
        return "F" if _has_inid_codes(text) else "E"
    return "F" if _has_inid_codes(text) else "E"


def _has_inid_codes(text: str) -> bool:
    """True if the document contains [22] and [45] INID anchors."""
    return bool(re.search(r"\[22\]", text) and re.search(r"\[45\]", text))


# =============================================================================
# SHARED LAYER 2: BODY SCAN FOR ISSUE DATE
# =============================================================================


def _body_scan_issue(all_lines: list, start_line: int = 15) -> Optional[str]:
    """
    Scan body text (from start_line onward) for 'Patented Month DD, YYYY'.
    Used as Layer 2 fallback by all eras.
    Returns MM/DD/YYYY or None.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in all_lines[start_line:]:
        m = re.search(pat, line)
        if m:
            return parse_date(m.group(1))
    return None


# =============================================================================
# ERA EXTRACTORS
# =============================================================================


def _extract_era_a(all_lines: list) -> ExtractionResult:
    """
    Era A (1–137,279): 1836–1872
    Issue:  'Letters Patent No. XXX, dated Month DD, YYYY'
    Filing: structurally absent — always MISSING
    """
    pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"

    for line in all_lines[:30]:
        m = re.search(pat, line)
        if m:
            issue = parse_date(m.group(1))
            if issue:
                return ExtractionResult(
                    issue_date=issue,
                    filing_date=None,
                    issue_confidence=Confidence.HIGH,
                    filing_confidence=Confidence.MISSING,
                    era="A",
                )

    # Layer 2 fallback
    issue = _body_scan_issue(all_lines)
    return ExtractionResult(
        issue_date=issue,
        filing_date=None,
        issue_confidence=Confidence.LOW if issue else Confidence.NONE,
        filing_confidence=Confidence.MISSING,
        era="A",
    )


def _extract_era_bcc(all_lines: list, era_tag: str) -> ExtractionResult:
    """
    Merged extractor for Era B (137,280–589,999), BC (590,000–935,999),
    and C (936,000–1,920,165): 1873–1933.

    Fix 8 — unified B/BC/C extraction:
        All three eras share identical filing logic.  They differ only in
        which issue anchor is physically present in the document:

            Era B only:    'Letters Patent No. X, dated Month DD, YYYY'
            Era BC:        either or both anchors may appear
            Era C only:    'Patented Month DD, YYYY'

        By trying both anchors for every patent in the range we correctly
        handle boundary patents that carry the wrong-era header, and we
        collapse three near-identical functions into one.

    Anchor reconciliation:
        Both found, agree   → issue = that date,            confidence = HIGH
        Both found, disagree→ issue = Patented date (newer), confidence = LOW
                              (LOW flags boundary / OCR disagreement for review)
        Only Patented found → confidence = MED
        Only dated found    → confidence = MED
        Neither found       → body-scan fallback,           confidence = LOW/NONE

    Filing:
        'Application filed Month DD, YYYY'  (Fix 7: also fled/f1led/filled)
        Stop before 'Renewed' — that date is not the filing date.
        confidence = HIGH when found, NONE otherwise.

    era_tag is passed in from era_classifier so output reporting stays
    consistent ("B", "BC", or "C") even though extraction is shared.
    """
    patented_pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    dated_pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"
    filing_pat = rf"[Aa]pplication\s+{_FILED_PAT}\s+{FLEXIBLE_DATE}"

    issue_patented = None
    issue_dated = None
    filing = None
    filing_conf = Confidence.NONE

    joined = _sliding_joins(all_lines[:40])

    for line in joined:
        if not issue_patented:
            m = re.search(patented_pat, line)
            if m:
                issue_patented = parse_date(m.group(1))

        if not issue_dated:
            m = re.search(dated_pat, line)
            if m:
                issue_dated = parse_date(m.group(1))

        if not filing:
            clean = re.split(r"\bRenewed\b", line, flags=re.I)[0]
            m = re.search(filing_pat, clean)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH

    # --- Reconcile the two issue anchors ---
    if issue_patented and issue_dated:
        if issue_patented == issue_dated:
            issue = issue_patented
            issue_conf = Confidence.HIGH  # both anchors agree
        else:
            issue = issue_patented  # prefer the newer "Patented" anchor
            issue_conf = Confidence.LOW  # disagreement — flag for review
    elif issue_patented:
        issue = issue_patented
        issue_conf = Confidence.MED
    elif issue_dated:
        issue = issue_dated
        issue_conf = Confidence.MED
    else:
        issue = _body_scan_issue(all_lines)
        issue_conf = Confidence.LOW if issue else Confidence.NONE

    return ExtractionResult(
        issue_date=issue,
        filing_date=filing,
        issue_confidence=issue_conf,
        filing_confidence=filing_conf,
        era=era_tag,
    )


def _extract_era_d(all_lines: list) -> ExtractionResult:
    """
    Era D (1,920,166–2,924,999): 1933–1960
    Issue:  'Patented Month DD, YYYY'
    Filing: 'Application Month DD, YYYY' — NO 'filed' keyword
    Divided apps: 'Original application ...' then 'this application ...'
                  Use 'this application' date.

    Fix 5: dehyphenate raw text before building line list so that
    'applica-\\ntion' collapses to 'application' before regex matching.
    The dehyphenation is applied in extract_dates() before passing
    all_lines here, so no change is needed in this function itself.
    """
    patented_pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    filing_primary = rf"\bApplication\s+{FLEXIBLE_DATE}"
    filing_divided = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    original_pat = r"[Oo]riginal\s+application"

    issue = None
    filing = None
    issue_conf = Confidence.NONE
    filing_conf = Confidence.NONE

    joined = _sliding_joins(all_lines[:50])

    for line in joined:
        if not issue:
            m = re.search(patented_pat, line)
            if m:
                issue = parse_date(m.group(1))
                issue_conf = Confidence.HIGH

        if not filing:
            m = re.search(filing_divided, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH
                continue

            if re.search(original_pat, line, re.I):
                continue

            m = re.search(filing_primary, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH

        if issue and filing:
            break

    if not issue:
        issue = _body_scan_issue(all_lines)
        issue_conf = Confidence.LOW if issue else Confidence.NONE

    return ExtractionResult(
        issue_date=issue,
        filing_date=filing,
        issue_confidence=issue_conf,
        filing_confidence=filing_conf,
        era="D",
    )


def _extract_era_e(all_lines: list) -> ExtractionResult:
    """
    Era E (2,925,000–3,625,113): 1960–1971

    One-column format (early E, ~1960–1965):
        Header line 1-3:  'Patented Month DD, YYYY'  (issue, appears very early)
        Body:             'Filed Month DD, YYYY, Ser. No.' (filing)

    Two-column format (late E, ~1965–1971):
        Header:  'Filed Month DD, YYYY, Ser. No.' (filing, first 30 lines)
        Body:    'Patented Month DD, YYYY' (issue, anywhere from line 0 onward)

    Divided apps: same 'this application' logic as Era D.
    """
    filing_pat_e = rf"\bFiled\s+{FLEXIBLE_DATE}"
    filing_pat_d = rf"\bApplication\s+{FLEXIBLE_DATE}"
    filing_divided = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    original_pat = r"[Oo]riginal\s+application"

    filing = None
    filing_conf = Confidence.NONE

    header = _sliding_joins(all_lines[:30])

    for line in header:
        if filing:
            break

        m = re.search(filing_divided, line, re.I)
        if m:
            filing = parse_date(m.group(1))
            filing_conf = Confidence.HIGH
            break

        if re.search(original_pat, line, re.I):
            continue

        m = re.search(filing_pat_e, line, re.I)
        if m:
            filing = parse_date(m.group(1))
            filing_conf = Confidence.HIGH
            break

    if not filing:
        for line in header:
            if re.search(original_pat, line, re.I):
                continue
            m = re.search(filing_pat_d, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.MED
                break

    # Fix 4: scan from line 0 to catch one-column format
    issue = _body_scan_issue(all_lines, start_line=0)

    # Deduplicate: body scan must not return the same date as filing
    if issue and issue == filing:
        issue = _body_scan_issue_skip(all_lines, skip_date=filing)

    issue_conf = Confidence.MED if issue else Confidence.NONE

    return ExtractionResult(
        issue_date=issue,
        filing_date=filing,
        issue_confidence=issue_conf,
        filing_confidence=filing_conf,
        era="E",
    )


def _body_scan_issue_skip(all_lines: list, skip_date: Optional[str]) -> Optional[str]:
    """
    Like _body_scan_issue but skips any occurrence of skip_date.
    Used by Era E to avoid returning the filing date as the issue date.
    """
    pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    for line in all_lines:
        m = re.search(pat, line)
        if m:
            candidate = parse_date(m.group(1))
            if candidate and candidate != skip_date:
                return candidate
    return None


def _extract_era_f(all_lines: list) -> ExtractionResult:
    """
    Era F (3,625,114+): 1971–present
    Issue:  [45] INID code
    Filing: [22] INID code

    Fix 6a — date-before-anchor:
        OCR frequently splits a [22]/[45] entry so the date value lands on
        the line ABOVE the INID code line, e.g.:
            'June 18, 1970'
            '[22] Filed:'
        dates_from() therefore searches from max(0, anchor_idx - 1) rather
        than anchor_idx, capturing both same-line and one-line-above cases.

    Fix 6b — priority follow-on covers date+country combos:
        PRIORITY_FOLLOWON now also suppresses lines of the form
        'Feb. 12, 1970 France' (date followed by country name) and
        'France Feb. 12, 1970' (country followed by date) that appear
        after a [30]/[32] line, preventing them from polluting candidates.

    Fix 1 — issue = file:
        After pairing, if issue == filing, try second candidate date for
        each anchor before accepting them as identical.

    Fix 2 — priority bleed:
        priority_block flag stays active across bare-date, country-name,
        and application-number follow-on lines after a priority INID code.

    Fix 3 — [45] before [22] in OCR (column-reversal):
        If idx_45 < idx_22, assignments are swapped back.
    """
    HEADER_LINES = 80

    # Lines that open a priority data block
    PRIORITY_START = re.compile(r"\[3[02]\]|\(3[02]\)|\[6[23]\]|\(6[23]\)", re.I)

    # Fix 6b: follow-on lines to suppress after a priority INID code.
    # Matches lines whose entire content is one of:
    #   - a country name (letters only)
    #   - an application number (digits/slashes/dots/dashes)
    #   - a bare date
    #   - date + country   (e.g. "Feb. 12, 1970 France")
    #   - country + date   (e.g. "France Feb. 12, 1970")
    _fd = FLEXIBLE_DATE[1:-1]  # strip outer capture parens for embedding
    PRIORITY_FOLLOWON = re.compile(
        rf"^(?:"
        rf"[A-Za-z]{{2,30}}"  # country name alone
        rf"|[\d,/\.\-]+"  # application number alone
        rf"|{_fd}"  # bare date alone
        rf"|{_fd}\s+[A-Za-z]{{2,30}}"  # date + country
        rf"|[A-Za-z]{{2,30}}\s+{_fd}"  # country + date
        rf")\s*$",
        re.I,
    )

    def is_priority_line(line: str, in_block: bool) -> tuple:
        """Return (should_skip, new_in_block)."""
        if PRIORITY_START.search(line):
            return True, True
        if in_block:
            if PRIORITY_FOLLOWON.match(line):
                return True, True
            return False, False  # structural line resets block
        return False, False

    header = all_lines[:HEADER_LINES]

    # --- Step 1: find anchor line indices, respecting priority blocks ---
    idx_22 = None
    idx_45 = None
    in_prio = False

    for i, line in enumerate(header):
        skip, in_prio = is_priority_line(line, in_prio)
        if skip:
            continue
        if idx_22 is None and ("[22]" in line or "(22)" in line):
            idx_22 = i
        if idx_45 is None and ("[45]" in line or "(45)" in line):
            idx_45 = i

    # --- Step 2: collect all dated lines, respecting priority blocks ---
    dated_lines = []
    in_prio = False
    for i, line in enumerate(header):
        skip, in_prio = is_priority_line(line, in_prio)
        if skip:
            continue
        d = first_date_in_line(line)
        if d:
            dated_lines.append((i, d))

    # --- Step 3: pair by position ---
    # Fix 6a: search from anchor_idx-1 to capture dates on the line
    # immediately above the INID code (common OCR split pattern).
    def dates_from(anchor_idx: Optional[int]) -> list:
        """
        All dates on lines >= max(0, anchor_idx - 1), in order.
        The -1 window catches the frequent OCR artifact where the date
        value is on the line directly above the '[22] Filed:' label.
        """
        if anchor_idx is None:
            return []
        search_from = max(0, anchor_idx - 1)
        return [date for line_idx, date in dated_lines if line_idx >= search_from]

    filing_candidates = dates_from(idx_22)
    issue_candidates = dates_from(idx_45)

    filing = filing_candidates[0] if filing_candidates else None
    issue = issue_candidates[0] if issue_candidates else None

    # Fix 1: if pairing produced the same date, try next candidates
    if issue and filing and issue == filing:
        alt_issue = issue_candidates[1] if len(issue_candidates) > 1 else None
        alt_filing = filing_candidates[1] if len(filing_candidates) > 1 else None

        if alt_issue and alt_issue != filing:
            issue = alt_issue
        elif alt_filing and alt_filing != issue:
            filing = alt_filing
        # If still equal, leave as-is — anomaly detector handles it

    # Fix 3: swap if [45] appeared before [22] in OCR (column-reversal)
    if issue and filing and idx_22 is not None and idx_45 is not None:
        if idx_45 < idx_22:
            issue, filing = filing, issue

    # Chronological sanity: issue must be >= filing
    dt_issue = _to_dt(issue)
    dt_filing = _to_dt(filing)
    if dt_issue and dt_filing and dt_filing > dt_issue:
        issue, filing = filing, issue

    issue_conf = Confidence.HIGH if issue else Confidence.NONE
    filing_conf = Confidence.HIGH if filing else Confidence.NONE

    # Layer 2 fallback for issue only
    if not issue:
        issue = _body_scan_issue(all_lines)
        issue_conf = Confidence.LOW if issue else Confidence.NONE

    return ExtractionResult(
        issue_date=issue,
        filing_date=filing,
        issue_confidence=issue_conf,
        filing_confidence=filing_conf,
        era="F",
    )


# =============================================================================
# SLIDING JOIN HELPER
# =============================================================================


def _sliding_joins(line_list: list) -> list:
    """
    Yield single lines plus 2-line and 3-line joins.
    Handles OCR line splits without global combinatorial explosion.
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
# PUBLIC ENTRY POINT
# =============================================================================


def extract_dates(text: str, patent_num: int) -> ExtractionResult:
    """
    Main entry point.

    Args:
        text:       Raw OCR text of the patent document.
        patent_num: Integer patent number (no leading zeros).

    Returns:
        ExtractionResult with dates, confidence levels, and era tag.
    """
    text = normalize_text(text)
    # Fix 5: dehyphenate before splitting into lines so that line-break
    # hyphens (e.g. 'applica-\\ntion') are collapsed before regex matching.
    text = dehyphenate(text)
    all_lines = lines(text)
    era = era_classifier(patent_num, text)

    extractors = {
        "A": _extract_era_a,
        "B": lambda lines: _extract_era_bcc(lines, "B"),
        "BC": lambda lines: _extract_era_bcc(lines, "BC"),
        "C": lambda lines: _extract_era_bcc(lines, "C"),
        "D": _extract_era_d,
        "E": _extract_era_e,
        "F": _extract_era_f,
    }

    return extractors[era](all_lines)

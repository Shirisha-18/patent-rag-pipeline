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
    Collapse OCR line-break hyphens: 'applica-\ntion' → 'application'.
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


def _extract_era_b(all_lines: list) -> ExtractionResult:
    """
    Era B (137,280–589,999): 1873–1897
    Issue:  'dated Month DD, YYYY' in specification line
    Filing: 'Application filed Month DD, YYYY'
            Stop before 'Renewed' — that date is not the filing date.
    """
    issue_pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"
    filing_pat = rf"[Aa]pplication\s+filed\s+{FLEXIBLE_DATE}"

    issue = None
    filing = None
    issue_conf = Confidence.NONE
    filing_conf = Confidence.NONE

    joined = _sliding_joins(all_lines[:40])

    for line in joined:
        if not issue:
            m = re.search(issue_pat, line)
            if m:
                issue = parse_date(m.group(1))
                issue_conf = Confidence.HIGH

        if not filing:
            clean = re.split(r"\bRenewed\b", line, flags=re.I)[0]
            m = re.search(filing_pat, clean)
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
        era="B",
    )


def _extract_era_bc(all_lines: list) -> ExtractionResult:
    """
    Era B→C (590,000–935,999): 1897–1909
    Two issue anchors may coexist — if both found and agree → HIGH.
    Issue:  'Patented Month DD, YYYY' header line  (newer anchor)
            'dated Month DD, YYYY' in spec line     (older anchor)
    Filing: 'Application filed Month DD, YYYY'
    """
    patented_pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    dated_pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"
    filing_pat = rf"[Aa]pplication\s+filed\s+{FLEXIBLE_DATE}"

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

    if issue_patented and issue_dated:
        if issue_patented == issue_dated:
            issue = issue_patented
            issue_conf = Confidence.HIGH
        else:
            issue = issue_patented  # prefer newer anchor
            issue_conf = Confidence.LOW  # disagreement — flag
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
        era="BC",
    )


def _extract_era_c(all_lines: list) -> ExtractionResult:
    """
    Era C (936,000–1,920,165): 1909–1933
    Issue:  'Patented Month DD, YYYY' on its own line near top
    Filing: 'Application filed Month DD, YYYY' on separate line
    """
    patented_pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    filing_pat = rf"[Aa]pplication\s+filed\s+{FLEXIBLE_DATE}"

    issue = None
    filing = None
    issue_conf = Confidence.NONE
    filing_conf = Confidence.NONE

    joined = _sliding_joins(all_lines[:40])

    for line in joined:
        if not issue:
            m = re.search(patented_pat, line)
            if m:
                issue = parse_date(m.group(1))
                issue_conf = Confidence.HIGH

        if not filing:
            clean = re.split(r"\bRenewed\b", line, flags=re.I)[0]
            m = re.search(filing_pat, clean)
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
        era="C",
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
    All [30]/[32]/[62]/[63] priority lines AND their following data lines
    are completely ignored.

    — issue = file:
        After pairing, if issue == filing, try assigning the *next distinct*
        date for each anchor before accepting them as identical.

    — foreign priority date bleed:
        Track a priority_block flag.  Once a line containing a priority INID
        code is seen, all subsequent lines that look like bare dates, country
        names, or application numbers are also suppressed — until a structural
        line (containing another INID code, a known keyword, or clearly not
        priority data) resets the flag.

    — [45] before [22] in OCR order:
        After pairing, if idx_45 < idx_22 (OCR column reversal), swap the
        assigned dates back.  Chronological sanity check is kept as secondary
        guard.
    """
    HEADER_LINES = 80

    # Lines that signal "this line and the next N lines are priority data"
    PRIORITY_START = re.compile(r"\[3[02]\]|\(3[02]\)|\[6[23]\]|\(6[23]\)", re.I)
    # A line looks like priority follow-on data if it is:
    #   - a bare date (matches FLEXIBLE_DATE with nothing else substantial)
    #   - a country name (letters only, short)
    #   - an application number (digits / slashes only)
    PRIORITY_FOLLOWON = re.compile(
        r"^(?:"
        r"[A-Za-z]{2,30}"  # country name
        r"|[\d,/\.\-]+"  # application number
        r"|"
        + FLEXIBLE_DATE[1:-1]  # bare date (strip outer parens)
        + r")\s*$",
        re.I,
    )

    def is_priority_line(line: str, in_block: bool) -> tuple:
        """Return (should_skip, new_in_block)."""
        if PRIORITY_START.search(line):
            return True, True
        if in_block:
            if PRIORITY_FOLLOWON.match(line):
                return True, True
            # Any other content — reset block
            return False, False
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
    def dates_after(anchor_idx: Optional[int]) -> list:
        """All dates on lines >= anchor_idx, in order."""
        if anchor_idx is None:
            return []
        return [date for line_idx, date in dated_lines if line_idx >= anchor_idx]

    filing_candidates = dates_after(idx_22)
    issue_candidates = dates_after(idx_45)

    filing = filing_candidates[0] if filing_candidates else None
    issue = issue_candidates[0] if issue_candidates else None

    # Fix 1: if pairing produced the same date, try next candidates
    if issue and filing and issue == filing:
        # Try second candidate for each
        alt_issue = issue_candidates[1] if len(issue_candidates) > 1 else None
        alt_filing = filing_candidates[1] if len(filing_candidates) > 1 else None

        if alt_issue and alt_issue != filing:
            issue = alt_issue
        elif alt_filing and alt_filing != issue:
            filing = alt_filing
        # If still equal, leave as-is (genuinely same date; anomaly detector handles it)

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
    # hyphens (e.g. 'applica-\ntion') are collapsed before regex matching.
    text = dehyphenate(text)
    all_lines = lines(text)
    era = era_classifier(patent_num, text)

    extractors = {
        "A": _extract_era_a,
        "B": _extract_era_b,
        "BC": _extract_era_bc,
        "C": _extract_era_c,
        "D": _extract_era_d,
        "E": _extract_era_e,
        "F": _extract_era_f,
    }

    return extractors[era](all_lines)

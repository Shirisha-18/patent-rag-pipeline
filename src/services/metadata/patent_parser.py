"""
patent_parser.py
----------------
Era-aware USPTO patent date extractor.

Architecture:
    1. era_classifier()     — routes patent to correct extractor by number + text
    2. extract_*()          — one function per era, explicit anchors only
    3. ExtractionResult     — standard output contract for all extractors
    4. extract_dates()      — public entry point

Era boundaries (patent number → year):
    A   1       –  134,503   (1836–1872)  "dated" in spec line, no filing date
    B   134,504 –  589,999   (1873–1897)  "dated" + "Application filed" same line
    B→C 590,000 –  935,999   (1897–1909)  both "Patented" header + "dated" spec line
    C   936,000 – 1,919,999  (1909–1933)  "Patented" own line + "Application filed"
    D  1,920,000 – 2,924,999 (1933–1960)  "Patented" own line + "Application" no filed
    E  2,925,000 – 3,649,999 (1960–1971)  "Filed" header + "Patented" in body
    F  3,650,000 +            (1971–now)   INID codes [22]/[45]
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

ERA_A_END = 134_503
ERA_B_END = 589_999
ERA_BC_END = 935_999
ERA_C_END = 1_919_999
ERA_D_END = 2_924_999
ERA_E_END = 3_649_999
# ERA_F = everything above ERA_E_END

FLEXIBLE_DATE = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"


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


# =============================================================================
# ERA CLASSIFIER
# =============================================================================


def era_classifier(patent_num: int, text: str) -> str:
    """
    Return era tag: "A" | "B" | "BC" | "C" | "D" | "E" | "F"

    For the E→F transition zone (numbers near ERA_E_END),
    we sniff the text for INID codes rather than relying on number alone.
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
        # Transition zone: some patents already have INID codes
        return "F" if _has_inid_codes(text) else "E"
    # Above ERA_E_END: almost always F, but verify
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
    Era A (1–134,503): 1836–1872
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
    Era B (134,504–589,999): 1873–1897
    Issue:  'dated Month DD, YYYY' in specification line
    Filing: 'Application filed Month DD, YYYY' on same specification line
            Stop before 'Renewed' — that date is not the filing date.
    """
    issue_pat = rf"[Ll]etters\s+[Pp]atent\s+No\.?\s*[\d,]+,?\s*dated\s+{FLEXIBLE_DATE}"
    filing_pat = rf"[Aa]pplication\s+filed\s+{FLEXIBLE_DATE}"

    issue = None
    filing = None
    issue_conf = Confidence.NONE
    filing_conf = Confidence.NONE

    # Join up to 3 consecutive lines to catch split OCR lines
    joined = _sliding_joins(all_lines[:40])

    for line in joined:
        if not issue:
            m = re.search(issue_pat, line)
            if m:
                issue = parse_date(m.group(1))
                issue_conf = Confidence.HIGH

        if not filing:
            # Strip everything from 'Renewed' onward before searching
            clean = re.split(r"\bRenewed\b", line, flags=re.I)[0]
            m = re.search(filing_pat, clean)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH

        if issue and filing:
            break

    # Layer 2 fallback for issue
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
    Era B→C (~590,000–935,999): 1897–1909
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

    # Resolve issue confidence
    if issue_patented and issue_dated:
        if issue_patented == issue_dated:
            issue = issue_patented
            issue_conf = Confidence.HIGH
        else:
            # Disagreement — prefer 'Patented' header, flag as LOW
            issue = issue_patented
            issue_conf = Confidence.LOW
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
    Era C (~936,000–1,919,999): 1909–1933
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
    Era D (~1,920,000–2,924,999): 1933–1960
    Issue:  'Patented Month DD, YYYY' — same as Era C
    Filing: 'Application Month DD, YYYY' — NO 'filed' keyword
            Special case: divided apps have 'Original application ...'
            followed by 'this application ...' — use 'this application' date.
    """
    patented_pat = rf"[Pp]atented\s+{FLEXIBLE_DATE}"
    # Primary filing: bare "Application <date>" (no "filed", no "Original")
    filing_primary = rf"\bApplication\s+{FLEXIBLE_DATE}"
    # Divided app: "this application <date>"
    filing_divided = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    # Must NOT match lines that start with "Original application"
    original_app_pat = r"[Oo]riginal\s+application"

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
            # Divided application — "this application" takes priority
            m = re.search(filing_divided, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH
                continue

            # Skip lines describing the original/parent application
            if re.search(original_app_pat, line, re.I):
                continue

            # Primary: bare "Application <date>"
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
    Era E (~2,925,000–3,649,999): 1960–1971
    Filing: 'Filed Month DD, YYYY, Ser. No.' in header (page 1)
    Issue:  'Patented Month DD, YYYY' appears in body (page 2, ~line 40–100)
            Use body scan as primary for issue.
    Divided apps: same 'this application' logic as Era D.
    """
    filing_pat = rf"\bFiled\s+{FLEXIBLE_DATE}"
    filing_divided = rf"\bthis\s+application\s+{FLEXIBLE_DATE}"
    original_pat = r"[Oo]riginal\s+application"

    filing = None
    filing_conf = Confidence.NONE

    # Filing is in the header — scan first 30 lines
    header = _sliding_joins(all_lines[:30])
    for line in header:
        if not filing:
            m = re.search(filing_divided, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH
                break

            if re.search(original_pat, line, re.I):
                continue

            m = re.search(filing_pat, line, re.I)
            if m:
                filing = parse_date(m.group(1))
                filing_conf = Confidence.HIGH
                break

    # Issue is in the body — Layer 2 is the PRIMARY scan for this era
    issue = _body_scan_issue(all_lines, start_line=20)
    issue_conf = Confidence.MED if issue else Confidence.NONE

    return ExtractionResult(
        issue_date=issue,
        filing_date=filing,
        issue_confidence=issue_conf,
        filing_confidence=filing_conf,
        era="E",
    )


def _extract_era_f(all_lines: list) -> ExtractionResult:
    """
    Era F (~3,650,000+): 1971–present
    Issue:  [45] INID code — date on same line or next line
    Filing: [22] INID code — date on same line or next line
    All [30]/[32]/[62]/[63] priority lines are completely ignored.
    """
    PRIORITY_CODES = {"[30]", "[32]", "[62]", "[63]", "(30)", "(32)", "(62)", "(63)"}

    issue = None
    filing = None
    issue_conf = Confidence.NONE
    filing_conf = Confidence.NONE

    def is_priority_line(line):
        return any(code in line for code in PRIORITY_CODES)

    def date_on_or_after(idx: int) -> Optional[str]:
        """Look for a date on line idx, or up to 3 lines after."""
        for offset in range(4):
            i = idx + offset
            if i >= len(all_lines):
                break
            if offset > 0 and is_priority_line(all_lines[i]):
                break
            d = first_date_in_line(all_lines[i])
            if d:
                return d
        return None

    for i, line in enumerate(all_lines[:120]):
        if is_priority_line(line):
            continue

        if not issue and ("[45]" in line or "(45)" in line):
            d = date_on_or_after(i)
            if d:
                issue = d
                issue_conf = Confidence.HIGH

        if not filing and ("[22]" in line or "(22)" in line):
            d = date_on_or_after(i)
            if d:
                filing = d
                filing_conf = Confidence.HIGH

        if issue and filing:
            break

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

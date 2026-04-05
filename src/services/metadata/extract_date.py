import os
import csv
import re
import unicodedata
from dotenv import load_dotenv
from datetime import datetime
from difflib import SequenceMatcher
from dateparser import parse

# Load .env file
load_dotenv()

# =================================================
# CONFIG
# =================================================
OCR_ROOT = os.getenv("OCR_ROOT")
REFERENCE_CSV = os.getenv("REFERENCE_CSV")
OUTPUT_CSV = os.path.join(
    os.getenv("OUTPUT_CSV_DIR"),
    f"patent_dates_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
)

EARLY_PATENT_NUM = 137279
FILING_START_DATE = datetime(1873, 4, 1)


# =================================================
# HELPERS
# =================================================
def normalize_patnum(patnum):
    return str(patnum).lstrip("0")


def get_first_text_file(folder_path):
    txt_files = [f for f in os.listdir(folder_path) if f.endswith("_text.txt")]
    return sorted(txt_files)[0] if txt_files else None


def normalize_text(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def split_date(date_str):
    if not date_str:
        return "", "", ""
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        return str(dt.year), str(dt.month), str(dt.day)
    except:
        return "", "", ""


def fuzzy_contains(line, target, threshold=0.72):
    words = re.findall(r"[A-Za-z]{3,}", line.lower())
    for word in words:
        if SequenceMatcher(None, word, target).ratio() >= threshold:
            return True
    return False


def find_alternate_dates(text, exclude_dates=[]):
    """Find all dates in text except those in exclude_dates"""
    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"
    exclude_dts = [parse(d) for d in exclude_dates if d]
    candidates = []
    for m in re.finditer(flexible_date, text, re.I):
        dt = parse(m.group(1))
        if dt and dt not in exclude_dts:
            candidates.append(dt)
    return sorted(set(candidates))


def safe_date(year, month, day):
    try:
        if year and month and day:
            return datetime(int(year), int(month), int(day))
    except:
        pass
    return None


# =================================================
# FILTER HELPERS
# =================================================
def is_likely_citation(line):
    """
    Returns True if line looks like a citation to another patent.

    KEY FIX: "Letters Patent No." is the patent's own header line —
    never a citation, even though it contains "Patent No.".
    All 1800s patents use this exact phrase in their header.
    """
    l = line.lower()
    if "letters patent no" in l:
        return False
    return (
        "prior patent" in l
        or "patent no" in l
        or "u.s. patent" in l
        or bool(re.search(r"\bno\.\s*\d{3,}", l))
    )


def is_noise_line(line):
    l = line.lower()
    return (
        "reissue" in l or "renewed" in l
        # NOTE: "division"/"continuation"/"foreign" removed here —
        # they are handled more precisely by is_priority_line and
        # invalid_filing_words. Keeping them here was too aggressive
        # and blocked valid lines like filing lines that mention serial numbers.
    )


def is_true_filing_line(line):
    l = line.lower()

    # Strong positive signals
    if "application filed" in l:
        return True
    if "[22]" in line or "(22)" in line:
        return True
    # FIX Case 6: "Filed Jan. 10, 1967, Ser. No." — standalone Filed + serial
    if re.search(r"\bfiled\b.{0,40}ser\.?\s*no", l):
        return True
    # FIX Case 4: "This application <date>" is the correct filing for divided apps
    if re.search(r"\bthis application\b", l):
        return True

    # Reject misleading contexts
    if any(
        x in l
        for x in [
            "original application",
            "divided",
            "division",
            "continuation",
            "parent",
        ]
    ):
        return False

    return "filed" in l


def is_reference_section(line):
    l = line.lower()
    return "references cited" in l or "u.s. patents" in l or "foreign patents" in l


def is_priority_line(line):
    """
    Detects lines belonging to foreign/related application priority blocks.
    Dates on these lines must NOT be used as filing or issue dates.
    """
    l = line.lower()
    return (
        "[30]" in line
        or "[32]" in line
        or "[62]" in line
        or "[63]" in line
        or "(30)" in line
        or "(32)" in line
        or "(62)" in line
        or "(63)" in line
        or "foreign application priority" in l
        or "foreign priority data" in l
        or "priority data" in l
        or "related u.s. application" in l
        or "continuation-in-part of" in l
        or "continuation of ser" in l
        or "division of ser" in l
        or "claims priority" in l
        # FIX Case 4: "Original application" lines contain dates that must be ignored
        or re.search(r"\boriginal application\b", l) is not None
    )


def is_original_application_line(line):
    """
    Specifically detects divided/continuation original application lines.
    These contain dates that should NOT be used as the filing date.
    The correct filing date for divided apps is on the 'This application' line.
    """
    l = line.lower()
    return bool(
        re.search(r"\boriginal application\b", l)
        or re.search(r"\bdivided\b.*\bser\.?\s*no\b", l)
        or (re.search(r"\bnow patent\b", l) and re.search(r"\bdated\b", l))
    )


# =================================================
# PRIORITY DATE HANDLER
# =================================================
def resolve_priority_dates(text):
    """
    For patents with foreign/related priority blocks:
    - Anchors to [22]/[45] INID codes explicitly
    - Handles date on same line OR next line(s) (up to 3 lines below)
    - Ignores all dates in [30]/[32]/[62]/[63] priority blocks
    - Chronology sanity: filing must be before issue

    Returns (patent_date, filing_date) or (None, None) if not applicable.
    """
    priority_markers = [
        "[32]",
        "[30]",
        "[62]",
        "[63]",
        "foreign application priority",
        "foreign application priority data",
        "foreign priority data",
        "priority data",
        "related u.s. application data",
        "related u.s. application",
    ]

    lower_text = text.lower()
    if not any(marker in lower_text for marker in priority_markers):
        return None, None

    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"
    header_lines = text.splitlines()[:120]

    patent_date = None
    filing_date = None

    def find_date_on_or_after(lines, start_idx):
        """
        Look for a date on lines[start_idx] first,
        then scan up to 3 following lines if not found on the same line.
        Stops early if it crosses into a priority block line.
        """
        for offset in range(4):
            idx = start_idx + offset
            if idx >= len(lines):
                break
            if is_priority_line(lines[idx]) and offset > 0:
                break
            m = re.search(flexible_date, lines[idx], re.I)
            if m:
                dt = parse(m.group(1))
                if dt:
                    return dt
        return None

    for i, line in enumerate(header_lines):
        stripped = line.strip()

        if is_priority_line(stripped):
            continue

        # Anchor [45] → issue date
        if not patent_date and ("[45]" in stripped or "(45)" in stripped):
            dt = find_date_on_or_after(header_lines, i)
            if dt:
                patent_date = dt.strftime("%m/%d/%Y")

        # Anchor [22] → filing date
        if not filing_date and ("[22]" in stripped or "(22)" in stripped):
            dt = find_date_on_or_after(header_lines, i)
            if dt:
                filing_date = dt.strftime("%m/%d/%Y")

    if patent_date and filing_date:
        p = parse(patent_date)
        f = parse(filing_date)
        if f and p and f > p:
            return None, None
        return patent_date, filing_date

    return None, None


# =================================================
# DATE EXTRACTION
# =================================================
def extract_patent_dates(text):
    text = normalize_text(text)
    lines = text.splitlines()
    combined_lines = []

    invalid_filing_words = [
        "renewed",
        "renewal",
        "priority",
        "foreign",
        "foreign application",
        "foreign priority",
        "reissue",
        "continuation",
        "continuation-in-part",
        "division",
        "divisional",
        "provisional",
        "pct",
        "international",
        "substitute",
        "corrected",
        "amended",
        # FIX Case 4: original application dates must not become filing date
        "original application",
    ]

    for i in range(len(lines)):
        line = lines[i].strip()
        if not line:
            continue
        combined_lines.append(line)
        if i + 1 < len(lines):
            combined_lines.append(line + " " + lines[i + 1].strip())
        if i + 2 < len(lines):
            combined_lines.append(
                line + " " + lines[i + 1].strip() + " " + lines[i + 2].strip()
            )

    patent_date = ""
    filed_date = ""
    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"

    patent_patterns = [
        # 1800s formats — must be first, most specific
        # Covers both:
        #   "Letters Patent No. 434,583, dated August 19, 1890."
        #   "SPECIFICATION forming part of Letters Patent No. X, dated <date>"
        rf"[Ll]etters\s+[Pp]atent\s+[Nn]o\.?\s*[\d,]+,?\s*dated\s+{flexible_date}",
        # Modern INID anchors
        rf"\(45\).*?{flexible_date}",
        rf"\[45\].*?{flexible_date}",
        # "Patented <date>" — used in early 1900s and mid-century patents
        rf"[Pp]atented\s+{flexible_date}",
        # Generic fallbacks
        rf"patent\w*\s+{flexible_date}",
        rf"patent\w*.*?{flexible_date}",
        rf"letters patent.*?dated\s+{flexible_date}",
        rf"dated\s+{flexible_date}",
    ]

    filed_patterns = [
        # Most explicit first: "Application filed <date>"  (1800s–early 1900s)
        rf"[Aa]pplication\s+filed\s+{flexible_date}",
        # FIX Case 4: "This application <date>, Ser. No." — divided/continuation correct filing
        rf"[Tt]his\s+application\s+{flexible_date}",
        # Modern INID anchors
        rf"\(22\).*?{flexible_date}",
        rf"\[22\].*?{flexible_date}",
        # FIX Case 6: "Filed <date>, Ser. No." — standalone Filed with serial
        rf"[Ff]iled\s+{flexible_date}",
        # Generic fallbacks
        rf"application.*?file\w*\s+{flexible_date}",
        rf"file\w*\s+{flexible_date}",
        rf"application\s+{flexible_date}",
    ]

    # ================= PRIMARY EXTRACTION =================
    for line in combined_lines:
        # Skip priority/related/original-application lines — dates here are not issue/filing
        if is_priority_line(line):
            continue

        if is_noise_line(line):
            continue

        if is_reference_section(line):
            break

        if not patent_date:
            for pat in patent_patterns:
                m = re.search(pat, line, re.I)
                if m and not is_likely_citation(line):
                    dt = parse(m.group(1))
                    if dt:
                        patent_date = dt.strftime("%m/%d/%Y")
                        break

        if not filed_date and is_true_filing_line(line):
            for pat in filed_patterns:
                m = re.search(pat, line, re.I)
                if m:
                    dt = parse(m.group(1))
                    if dt:
                        filed_date = dt.strftime("%m/%d/%Y")
                        break

        if patent_date and filed_date:
            break

    # ================= FUZZY RESCUE =================
    if not patent_date or not filed_date:
        for line in combined_lines:
            if is_priority_line(line):
                continue

            m = re.search(flexible_date, line, re.I)
            if not m:
                continue

            dt = parse(m.group(1))
            if not dt:
                continue

            formatted = dt.strftime("%m/%d/%Y")
            lower_line = line.lower()

            if (
                not filed_date
                and is_true_filing_line(line)
                and (
                    fuzzy_contains(lower_line, "filed")
                    or fuzzy_contains(lower_line, "application")
                    or "[22]" in line
                    or "(22)" in line
                )
            ):
                if not any(word in lower_line for word in invalid_filing_words):
                    filed_date = formatted
                    continue

            # FIX Case 3: guard "issued" in fuzzy rescue — only use it if NOT a citation line
            # "patented" is safe; "issued" is risky because citations say "issued to X on <date>"
            if (
                not patent_date
                and not is_likely_citation(line)
                and (
                    fuzzy_contains(lower_line, "patented")
                    or "[45]" in line
                    or "(45)" in line
                    # "issued" only allowed if no person/assignee name context nearby
                    or (
                        fuzzy_contains(lower_line, "issued")
                        and not re.search(r"issued to\b", lower_line)
                        and not re.search(r"issued [A-Z][a-z]", line)
                    )
                )
            ):
                if "renewed" not in lower_line:
                    patent_date = formatted
                    continue

    # ================= SECOND PASS (FIXED POSITION) =================
    # Handles patents where "Patented <date>" only appears deep in the document body
    # e.g. 1910s–1960s era patents like 1,294,122 / 3,445,944 / 3,052,062
    if not patent_date:
        for i, line in enumerate(lines):
            if i < 80:
                continue
            if is_priority_line(line):
                continue
            # FIX Case 3: use "patented" only, not "issued" — too risky in body text
            if "patented" in line.lower():
                m = re.search(flexible_date, line, re.I)
                if m:
                    dt = parse(m.group(1))
                    if dt:
                        patent_date = dt.strftime("%m/%d/%Y")
                        break

    # ================= MULTI-LINE FALLBACK =================
    if not patent_date:
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        for i, line in enumerate(lines):
            if is_priority_line(line):
                continue
            for month in months:
                if month in line:
                    day, year = None, None
                    for j in range(1, 6):
                        if i + j < len(lines):
                            m_day = re.search(r"\b(\d{1,2})\b", lines[i + j])
                            if m_day:
                                day = m_day.group(1)
                                for k in range(i + j + 1, min(i + j + 6, len(lines))):
                                    m_year = re.search(r"\b(\d{4})\b", lines[k])
                                    if m_year:
                                        year = m_year.group(1)
                                        break
                                break
                    if day and year:
                        patent_date = f"{month} {day}, {year}"
                        break
            if patent_date:
                break

    # ================= SANITY CHECK =================
    if patent_date and filed_date:
        try:
            p = parse(patent_date)
            f = parse(filed_date)
            if f > p and abs((f - p).days) > 365:
                filed_date = ""
        except:
            pass

    return patent_date, filed_date


# =================================================
# LOAD REFERENCE CSV
# =================================================
def load_csv_dict(path):
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = normalize_patnum(row["patnum"])
            data[key] = row
    return data


# =================================================
# COMPARISON LOGIC
# =================================================
def compare_dates_with_flags(extracted_row, reference_row):
    patnum_int = int(normalize_patnum(extracted_row["patnum"]))

    if not reference_row:
        return "Missing in reference", "Missing in reference", "Missing"

    if patnum_int < EARLY_PATENT_NUM:
        patent_status = (
            "Yes"
            if (
                extracted_row["iyear"] == reference_row.get("iyear")
                and extracted_row["imonth"] == reference_row.get("imonth")
                and extracted_row["iday"] == reference_row.get("iday")
            )
            else "No"
        )
        return (
            patent_status,
            "Missing in patent",
            "Correct" if patent_status == "Yes" else "Wrong",
        )

    issue_ref_missing = not (
        reference_row.get("iyear")
        and reference_row.get("imonth")
        and reference_row.get("iday")
    )
    filing_ref_missing = not (
        reference_row.get("fyear")
        and reference_row.get("fmonth")
        and reference_row.get("fday")
    )

    issue_status = (
        "Missing in reference"
        if issue_ref_missing
        else (
            "No"
            if (
                extracted_row["iyear"] != reference_row["iyear"]
                or extracted_row["imonth"] != reference_row["imonth"]
                or extracted_row["iday"] != reference_row["iday"]
            )
            else "Yes"
        )
    )

    filing_status = (
        "Missing in reference"
        if filing_ref_missing
        else (
            "No"
            if (
                extracted_row["fyear"] != reference_row["fyear"]
                or extracted_row["fmonth"] != reference_row["fmonth"]
                or extracted_row["fday"] != reference_row["fday"]
            )
            else "Yes"
        )
    )

    flag = "Wrong" if "No" in (issue_status, filing_status) else "Correct"
    return issue_status, filing_status, flag


# =================================================
# MONOTONICITY RESCUE
# =================================================
def rescue_monotonicity(text, previous_issue_date, current_issue_dt):
    """
    When issue < previous issue, scan the document for another date that:
      - Is >= previous_issue_date  (respects monotonicity)
      - Is > current extracted filing date (valid issue > filing ordering)
      - Is not on a priority/citation/noise line

    Returns a new issue date string (MM/DD/YYYY) or None if not found.

    """
    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"
    lines = text.splitlines()
    candidates = []

    for line in lines:
        if is_priority_line(line):
            continue
        if is_likely_citation(line):
            continue
        if is_reference_section(line):
            break
        for m in re.finditer(flexible_date, line, re.I):
            dt = parse(m.group(1))
            if dt and dt >= previous_issue_date and dt != current_issue_dt:
                candidates.append(dt)

    if candidates:
        # Prefer the smallest valid date (earliest that still satisfies monotonicity)
        best = min(candidates)
        return best.strftime("%m/%d/%Y")

    return None


# =================================================
# MAIN EXECUTION
# =================================================
def run():
    extracted_rows = []
    # Cache text per patent for rescue re-use
    text_cache = {}

    for folder in sorted(os.listdir(OCR_ROOT)):
        folder_path = os.path.join(OCR_ROOT, folder)
        if not os.path.isdir(folder_path):
            continue

        first_txt = get_first_text_file(folder_path)
        if not first_txt:
            continue

        with open(
            os.path.join(folder_path, first_txt), "r", encoding="utf-8", errors="ignore"
        ) as f:
            text = f.read()

        text_cache[folder] = text

        priority_patent, priority_filing = resolve_priority_dates(text)

        if priority_patent and priority_filing:
            patent_date = priority_patent
            filed_date = priority_filing
        else:
            patent_date, filed_date = extract_patent_dates(text)

        pyear, pmonth, pday = split_date(patent_date)
        fyear, fmonth, fday = split_date(filed_date)

        extracted_rows.append(
            {
                "patnum": folder,
                "iyear": pyear,
                "imonth": pmonth,
                "iday": pday,
                "fyear": fyear,
                "fmonth": fmonth,
                "fday": fday,
            }
        )

        print(
            f"[OK] {folder} | Patent: {patent_date or 'N/A'} | Filed: {filed_date or 'N/A'}"
        )

    reference_dict = load_csv_dict(REFERENCE_CSV)
    final_rows = []
    previous_issue_date = None

    for row in extracted_rows:
        ref = reference_dict.get(normalize_patnum(row["patnum"]))
        issue_comp, filing_comp, validation = compare_dates_with_flags(row, ref)

        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
        filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])

        # ===== RESCUE LOGIC (reference mismatch) =====
        if issue_comp == "No" or filing_comp == "No":
            text = text_cache.get(row["patnum"], "")
            current_issue = (
                f"{row['imonth']}/{row['iday']}/{row['iyear']}" if row["iyear"] else ""
            )
            current_filing = (
                f"{row['fmonth']}/{row['fday']}/{row['fyear']}" if row["fyear"] else ""
            )
            candidates = find_alternate_dates(
                text,
                exclude_dates=[current_issue]
                if issue_comp == "Yes"
                else [current_filing],
            )
            for dt in candidates:
                if (
                    issue_comp == "Yes"
                    and filing_comp == "No"
                    and issue_dt
                    and dt
                    and dt < issue_dt
                ):
                    row["fyear"], row["fmonth"], row["fday"] = split_date(
                        dt.strftime("%m/%d/%Y")
                    )
                    filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])
                    break
                elif (
                    issue_comp == "No"
                    and filing_comp == "Yes"
                    and filing_dt
                    and dt
                    and dt > filing_dt
                ):
                    row["iyear"], row["imonth"], row["iday"] = split_date(
                        dt.strftime("%m/%d/%Y")
                    )
                    issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
                    break
                elif issue_comp == "No" and filing_comp == "No" and dt:
                    filing_dt_candidate = dt
                    issue_dt_candidate = None
                    for dt2 in candidates:
                        if dt2 and dt2 > filing_dt_candidate:
                            issue_dt_candidate = dt2
                            break
                    if filing_dt_candidate and issue_dt_candidate:
                        row["fyear"], row["fmonth"], row["fday"] = split_date(
                            filing_dt_candidate.strftime("%m/%d/%Y")
                        )
                        row["iyear"], row["imonth"], row["iday"] = split_date(
                            issue_dt_candidate.strftime("%m/%d/%Y")
                        )
                        filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])
                        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
                        break

        # Recompute safe dates after rescue
        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
        filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])

        patnum_int = int(normalize_patnum(row["patnum"]))
        is_early_patent = patnum_int < EARLY_PATENT_NUM

        # FIX Case 8: filing_comp == "Missing in patent" means filing date is
        # structurally absent (early patents predate filing records) — not an anomaly
        filing_structurally_absent = filing_comp == "Missing in patent"

        # ===== ANOMALY LOGIC =====
        anomaly_flag = "OK"
        EARLIEST_HISTORICAL_DATE = datetime(1843, 7, 26)

        # ===== FIRST PATENT =====
        if previous_issue_date is None:
            if issue_dt:
                if is_early_patent and issue_dt < EARLIEST_HISTORICAL_DATE:
                    anomaly_flag = "old patent"
                elif (
                    not is_early_patent
                    and not filing_structurally_absent
                    and filing_dt
                    and issue_dt == filing_dt
                ):
                    anomaly_flag = "issue = file"

        # ===== SUBSEQUENT PATENTS =====
        else:
            if issue_dt:
                # 1. issue = file — explicit, checked first (Case 1 priority fix)
                if (
                    not is_early_patent
                    and not filing_structurally_absent
                    and filing_dt
                    and issue_dt == filing_dt
                ):
                    anomaly_flag = "issue = file"

                # 2. Monotonicity: issue dates must be non-decreasing
                elif issue_dt < previous_issue_date:
                    # FIX Case 7: attempt rescue before accepting the flag —
                    # scan current document for a date >= previous_issue_date
                    text = text_cache.get(row["patnum"], "")
                    rescued = rescue_monotonicity(text, previous_issue_date, issue_dt)
                    if rescued:
                        row["iyear"], row["imonth"], row["iday"] = split_date(rescued)
                        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
                        # Re-check issue = file after rescue
                        if (
                            not is_early_patent
                            and not filing_structurally_absent
                            and filing_dt
                            and issue_dt == filing_dt
                        ):
                            anomaly_flag = "issue = file"
                        # Re-check issue < file after rescue
                        elif (
                            not is_early_patent
                            and not filing_structurally_absent
                            and filing_dt
                            and filing_dt >= FILING_START_DATE
                            and issue_dt
                            and issue_dt < filing_dt
                        ):
                            anomaly_flag = "issue < file"
                        else:
                            anomaly_flag = "OK"
                    else:
                        anomaly_flag = "issue < previous issue"

                # 3. Historical floor for early patents
                elif is_early_patent and issue_dt < EARLIEST_HISTORICAL_DATE:
                    anomaly_flag = "old patent"

                # 4. Modern patent: issue before filing
                elif (
                    not is_early_patent
                    and not filing_structurally_absent
                    and filing_dt
                    and filing_dt >= FILING_START_DATE
                    and issue_dt < filing_dt
                ):
                    anomaly_flag = "issue < file"

                # validation == "Wrong" alone is NOT an anomaly

        # Update tracker for next patent's monotonicity check
        if issue_dt:
            previous_issue_date = issue_dt

        # ===== FINAL ROW ASSEMBLY =====
        final_rows.append(
            {
                **row,
                "issue_date_comparison": issue_comp,
                "filing_date_comparison": filing_comp,
                "validation_result": validation,
                "anomaly_flag": anomaly_flag,
            }
        )

    if final_rows:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "patnum",
                    "iyear",
                    "imonth",
                    "iday",
                    "fyear",
                    "fmonth",
                    "fday",
                    "issue_date_comparison",
                    "filing_date_comparison",
                    "validation_result",
                    "anomaly_flag",
                ],
            )
            writer.writeheader()
            writer.writerows(final_rows)

        print(f"\n✅ Done! CSV saved to:\n{OUTPUT_CSV}")


if __name__ == "__main__":
    run()

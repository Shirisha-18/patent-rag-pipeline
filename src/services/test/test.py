import os
import csv
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from dateparser import parse

# =================================================
# CONFIG
# =================================================
OCR_ROOT = r"C:\Users\shiri\Dropbox\ocr_patents\ocr_patents\random_sample"
REFERENCE_CSV = r"C:\Users\shiri\Dropbox\ocr_patents\patents_fyear_iyear.csv"
OUTPUT_CSV = rf"C:\Users\shiri\Dropbox\ocr_patents\info\patent_dates_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

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
# NEW FILTER HELPERS (SAFE ADDITIONS)
# =================================================
def is_likely_citation(line):
    l = line.lower()
    return (
        "prior patent" in l
        or "patent no" in l
        or "u.s. patent" in l
        or re.search(r"\bno\.\s*\d{3,}", l)
    )


def is_noise_line(line):
    l = line.lower()
    return (
        "renewed" in l
        or "reissue" in l
        or "division" in l
        or "divided" in l
        or "continuation" in l
        or "foreign" in l
    )


def is_true_filing_line(line):
    l = line.lower()

    # Strong signals
    if "application filed" in l:
        return True
    if "[22]" in line or "(22)" in line:
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


# =================================================
# PRIORITY DATE HANDLER
# =================================================
def resolve_priority_dates(text):

    priority_markers = [
        "[32]",
        "[30]",
        "foreign application priority",
        "foreign application priority data",
        "foreign priority data",
        "priority data",
    ]

    lower_text = text.lower()

    # If no priority block exists, skip
    if not any(marker in lower_text for marker in priority_markers):
        return None, None

    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})"

    dates = []

    # Only scan header area to avoid citation dates
    header_lines = text.splitlines()[:120]
    header_text = "\n".join(header_lines)

    for m in re.finditer(flexible_date, header_text, re.I):
        dt = parse(m.group(1))
        if dt:
            dates.append(dt)

    dates = sorted(set(dates))

    # Need at least 3 dates
    if len(dates) < 3:
        return None, None

    filing_dt = min(dates)
    patent_dt = max(dates)

    return (patent_dt.strftime("%m/%d/%Y"), filing_dt.strftime("%m/%d/%Y"))


# =================================================
# DATE EXTRACTION (High recall + multi-line, ignoring Renewed)
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
        rf"patent\w*\s+{flexible_date}",
        rf"patent\w*.*?{flexible_date}",
        rf"letters patent.*?dated\s+{flexible_date}",
        rf"dated\s+{flexible_date}",
        rf"\(45\).*?{flexible_date}",
        rf"\[45\].*?{flexible_date}",
    ]

    filed_patterns = [
        rf"application.*?file\w*\s+{flexible_date}",
        rf"file\w*\s+{flexible_date}",
        rf"\(22\).*?{flexible_date}",
        rf"\[22\].*?{flexible_date}",
        rf"application\s+{flexible_date}",
    ]

    # ================= PRIMARY EXTRACTION =================
    for line in combined_lines:
        lower_line = line.lower()

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

            if (
                not patent_date
                and not is_likely_citation(line)
                and (
                    fuzzy_contains(lower_line, "patented")
                    or fuzzy_contains(lower_line, "issued")
                    or "[45]" in line
                    or "(45)" in line
                )
            ):
                if "renewed" not in lower_line:
                    patent_date = formatted
                    continue

    # ================= SECOND PASS (FIXED POSITION) =================
    if not patent_date:
        for i, line in enumerate(lines):
            if i < 80:
                continue
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

    # Patent missing in reference
    if not reference_row:
        return "Missing in reference", "Missing in reference", "Missing"

    # Early patents
    if patnum_int < EARLY_PATENT_NUM:
        patent_status = (
            "No"
            if (
                extracted_row["iyear"] == reference_row.get("iyear")
                and extracted_row["imonth"] == reference_row.get("imonth")
                and extracted_row["iday"] == reference_row.get("iday")
            )
            else "Yes"
        )
        return (
            patent_status,
            "Missing in patent",
            "Correct" if patent_status == "No" else "Wrong",
        )

    # Missing reference dates
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
            "Yes"
            if (
                extracted_row["iyear"] != reference_row["iyear"]
                or extracted_row["imonth"] != reference_row["imonth"]
                or extracted_row["iday"] != reference_row["iday"]
            )
            else "No"
        )
    )

    filing_status = (
        "Missing in reference"
        if filing_ref_missing
        else (
            "Yes"
            if (
                extracted_row["fyear"] != reference_row["fyear"]
                or extracted_row["fmonth"] != reference_row["fmonth"]
                or extracted_row["fday"] != reference_row["fday"]
            )
            else "No"
        )
    )

    flag = "Wrong" if "Yes" in (issue_status, filing_status) else "Correct"
    return issue_status, filing_status, flag


# =================================================
# SEMANTIC FLIP FOR OUTPUT
# =================================================
def flip_semantics(value):
    if value == "Yes":
        return "No"
    if value == "No":
        return "Yes"
    return value


# =================================================
# MAIN EXECUTION
# =================================================
def run():
    extracted_rows = []

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

        # ---- PRIORITY DATE FIX ----
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

        # ===== RESCUE LOGIC =====
        if issue_comp == "Yes" or filing_comp == "Yes":
            folder_path = os.path.join(OCR_ROOT, row["patnum"])
            first_txt = get_first_text_file(folder_path)
            if first_txt:
                with open(
                    os.path.join(folder_path, first_txt),
                    "r",
                    encoding="utf-8",
                    errors="ignore",
                ) as f:
                    text = f.read()
                current_issue = (
                    f"{row['imonth']}/{row['iday']}/{row['iyear']}"
                    if row["iyear"]
                    else ""
                )
                current_filing = (
                    f"{row['fmonth']}/{row['fday']}/{row['fyear']}"
                    if row["fyear"]
                    else ""
                )
                candidates = find_alternate_dates(
                    text,
                    exclude_dates=[current_issue]
                    if issue_comp == "No"
                    else [current_filing],
                )
                for dt in candidates:
                    if (
                        issue_comp == "No"
                        and filing_comp == "Yes"
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
                        issue_comp == "Yes"
                        and filing_comp == "No"
                        and filing_dt
                        and dt
                        and dt > filing_dt
                    ):
                        row["iyear"], row["imonth"], row["iday"] = split_date(
                            dt.strftime("%m/%d/%Y")
                        )
                        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
                        break
                    elif issue_comp == "Yes" and filing_comp == "Yes" and dt:
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
                            filing_dt = safe_date(
                                row["fyear"], row["fmonth"], row["fday"]
                            )
                            issue_dt = safe_date(
                                row["iyear"], row["imonth"], row["iday"]
                            )
                            break

        # Recompute safe dates
        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
        filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])

        patnum_int = int(normalize_patnum(row["patnum"]))
        is_early_patent = patnum_int < EARLY_PATENT_NUM

        # ===== ANOMALY LOGIC =====
        anomaly_flag = "OK"

        # Historical start date for early patents
        EARLIEST_HISTORICAL_DATE = datetime(1843, 7, 26)

        # ===== FIRST PATENT =====
        if previous_issue_date is None:
            if issue_dt:
                if is_early_patent and issue_dt < EARLIEST_HISTORICAL_DATE:
                    anomaly_flag = "Error"  # before known historical range
                elif validation == "Wrong":
                    anomaly_flag = "Error"  # extracted issue date inconsistent
                else:
                    anomaly_flag = "OK"  # first patent passes historical + validation

        # ===== SUBSEQUENT PATENTS =====
        else:
            if issue_dt:
                # 1. Monotonicity check: current issue date >= previous patent issue date
                if issue_dt < previous_issue_date:
                    anomaly_flag = "Error"
                # 2. Historical range check for early patents
                elif is_early_patent and issue_dt < EARLIEST_HISTORICAL_DATE:
                    anomaly_flag = "Error"
                # 3 Validation check
                elif validation == "Wrong":
                    anomaly_flag = "Error"
                # 4 Modern patent filing vs issue check
                elif (
                    not is_early_patent
                    and filing_dt
                    and filing_dt >= FILING_START_DATE
                    and issue_dt < filing_dt
                ):
                    anomaly_flag = "Error"
                else:
                    anomaly_flag = "OK"

        # Update previous_issue_date for next iteration
        if issue_dt:
            previous_issue_date = issue_dt

        # ===== EARLY PATENT OUTPUT CONTROL =====
        if is_early_patent:
            issue_filing_same_date_value = ""
            issue_greater_than_filing_value = ""
        else:
            issue_filing_same_date_value = (
                "Yes" if issue_dt and filing_dt and issue_dt == filing_dt else "No"
            )
            issue_greater_than_filing_value = (
                "Yes" if issue_dt and filing_dt and issue_dt > filing_dt else "No"
            )

        # ===== FINAL ROW ASSEMBLY =====
        final_rows.append(
            {
                **row,
                "issue_date_comparison": flip_semantics(issue_comp),
                "filing_date_comparison": flip_semantics(filing_comp),
                "validation_result": validation,
                "anomaly_flag": anomaly_flag,
                "issue_filing_same_date": issue_filing_same_date_value,
                "issue_greater_than_filing": issue_greater_than_filing_value,
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
                    "issue_filing_same_date",
                    "issue_greater_than_filing",
                ],
            )
            writer.writeheader()
            writer.writerows(final_rows)

        print(f"\n✅ Done! CSV saved to:\n{OUTPUT_CSV}")


if __name__ == "__main__":
    run()

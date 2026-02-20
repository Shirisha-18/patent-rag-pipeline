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

OCR_ROOT = r"C:\Users\shirisha.biyyala\Dropbox\ocr_patents\ocr_patents\random_sample"
REFERENCE_CSV = r"C:\Users\shirisha.biyyala\Dropbox\ocr_patents\patents_fyear_iyear.csv"
OUTPUT_CSV = rf"C:\Users\shirisha.biyyala\Dropbox\ocr_patents\info\patent_dates_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

EARLY_PATENT_NUM = 137279


# =================================================
# HELPERS
# =================================================


def normalize_patnum(patnum):
    return str(patnum).lstrip("0")


def safe_date(y, m, d):
    try:
        return datetime(int(y), int(m), int(d))
    except:
        return None


def split_date(date_str):
    if not date_str:
        return "", "", ""
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        return str(dt.year), str(dt.month), str(dt.day)
    except:
        return "", "", ""


def normalize_text(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def fuzzy_contains(line, target, threshold=0.72):
    words = re.findall(r"[A-Za-z]{3,}", line.lower())
    for word in words:
        if SequenceMatcher(None, word, target).ratio() >= threshold:
            return True
    return False


# =================================================
# RESTORED HIGH-RECALL EXTRACTION
# =================================================


def extract_patent_dates(text):

    text = normalize_text(text)
    lines = text.splitlines()

    # --- 3-line merge (original strong logic) ---
    combined_lines = []
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

    flexible_date = r"([A-Za-z]{3,9}\.?\s+\d{1,2}[,\.]?\s+\d{4})"

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

    # --- Primary extraction ---
    for line in combined_lines:
        if not patent_date:
            for pat in patent_patterns:
                m = re.search(pat, line, re.I)
                if m:
                    dt = parse(m.group(1))
                    if dt:
                        patent_date = dt.strftime("%m/%d/%Y")
                        break

        if not filed_date:
            for pat in filed_patterns:
                m = re.search(pat, line, re.I)
                if m:
                    dt = parse(m.group(1))
                    if dt:
                        filed_date = dt.strftime("%m/%d/%Y")
                        break

        if patent_date and filed_date:
            break

    # --- Fuzzy rescue (original logic) ---
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

            if not filed_date and (
                fuzzy_contains(lower_line, "filed")
                or fuzzy_contains(lower_line, "application")
                or "[22]" in line
            ):
                filed_date = formatted
                continue

            if not patent_date and (
                fuzzy_contains(lower_line, "patented")
                or fuzzy_contains(lower_line, "issued")
                or "[45]" in line
            ):
                patent_date = formatted
                continue

    # --- Sanity check ---
    if patent_date and filed_date:
        try:
            if parse(filed_date) > parse(patent_date):
                filed_date = ""
        except:
            pass

    return patent_date, filed_date


# =================================================
# LOAD REFERENCE
# =================================================


def load_reference(path):
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


def compare_dates(extracted, reference):

    if not reference:
        return "Missing in reference", "Missing in reference"

    # Issue
    if not extracted["iyear"]:
        issue = "Missing in patent"
    elif not reference.get("iyear"):
        issue = "Missing in reference"
    elif (
        extracted["iyear"] == reference["iyear"]
        and extracted["imonth"] == reference["imonth"]
        and extracted["iday"] == reference["iday"]
    ):
        issue = "Match"
    else:
        issue = "No Match"

    # Filing
    if not extracted["fyear"]:
        filing = "Missing in patent"
    elif not reference.get("fyear"):
        filing = "Missing in reference"
    elif (
        extracted["fyear"] == reference["fyear"]
        and extracted["fmonth"] == reference["fmonth"]
        and extracted["fday"] == reference["fday"]
    ):
        filing = "Match"
    else:
        filing = "No Match"

    return issue, filing


def compute_validation(issue, filing):

    if "Missing" in issue or "Missing" in filing:
        return "MISSING_DATA"

    if issue == "Match" and filing == "Match":
        return "VALID"

    if issue == "Match" or filing == "Match":
        return "PARTIAL_MISMATCH"

    return "MISMATCH"


# =================================================
# MAIN
# =================================================


def run():

    extracted_rows = []

    for folder in sorted(os.listdir(OCR_ROOT)):
        folder_path = os.path.join(OCR_ROOT, folder)
        if not os.path.isdir(folder_path):
            continue

        txt = [f for f in os.listdir(folder_path) if f.endswith("_text.txt")]
        if not txt:
            continue

        with open(
            os.path.join(folder_path, sorted(txt)[0]),
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as f:
            text = f.read()

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

    reference_dict = load_reference(REFERENCE_CSV)

    final_rows = []
    previous_issue_date = None

    for row in extracted_rows:
        ref = reference_dict.get(normalize_patnum(row["patnum"]))
        issue_comp, filing_comp = compare_dates(row, ref)
        validation = compute_validation(issue_comp, filing_comp)

        issue_dt = safe_date(row["iyear"], row["imonth"], row["iday"])
        filing_dt = safe_date(row["fyear"], row["fmonth"], row["fday"])

        anomaly_flag = "OK"

        if issue_dt and previous_issue_date and issue_dt < previous_issue_date:
            anomaly_flag = "Error"

        if issue_dt and filing_dt and issue_dt < filing_dt:
            anomaly_flag = "Error"

        if issue_dt:
            previous_issue_date = issue_dt

        final_rows.append(
            {
                **row,
                "issue_date_comparison": issue_comp,
                "filing_date_comparison": filing_comp,
                "validation_result": validation,
                "anomaly_flag": anomaly_flag,
                "issue_filing_same_date": "YES"
                if issue_dt and filing_dt and issue_dt == filing_dt
                else "NO",
                "issue_greater_than_filing": "YES"
                if issue_dt and filing_dt and issue_dt > filing_dt
                else "NO",
            }
        )

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

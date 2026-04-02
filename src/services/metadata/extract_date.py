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

FILING_START_DATE = datetime(1873, 4, 1)

# =================================================
# HELPERS
# =================================================


def normalize_patnum(patnum):
    return patnum.lstrip("0")


def get_first_text_file(folder_path):
    txt_files = [f for f in os.listdir(folder_path) if f.endswith("_text.txt")]
    return sorted(txt_files)[0] if txt_files else None


def split_date(date_str):
    if not date_str:
        return "", "", ""
    dt = datetime.strptime(date_str, "%m/%d/%Y")
    return str(dt.year), str(dt.month), str(dt.day)


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


def extract_date_from_line(line):
    pattern = r"([A-Za-z]{3,9}\.?\s+\d{1,2}[,\.]?\s+\d{4})"
    m = re.search(pattern, line, re.I)
    if m:
        dt = parse(m.group(1))
        if dt:
            return dt.strftime("%m/%d/%Y")
    return ""


# =================================================
# DATE EXTRACTION
# =================================================


def extract_patent_dates(text, patnum):
    text = normalize_text(text)
    lines = text.splitlines()

    patent_date = ""
    filed_date = ""

    try:
        patnum_int = int(normalize_patnum(patnum))
    except:
        patnum_int = 0

    # =================================================
    # RANGE-SPECIFIC LAYOUT RULES
    # =================================================

    # -------- 3543618–3544118 --------
    if 3543618 <= patnum_int <= 3544118:
        for line in lines:
            if "(22" in line or "[22" in line:
                filed_date = extract_date_from_line(line)
            if "(45" in line or "[45" in line:
                patent_date = extract_date_from_line(line)

    # -------- 3558791–3634888 --------
    elif 3558791 <= patnum_int <= 3634888:
        for line in lines:
            if "22 Filed" in line or "(22)" in line:
                filed_date = extract_date_from_line(line)
            if "45 Patented" in line or "(45)" in line:
                patent_date = extract_date_from_line(line)

    # -------- 3634889–3695820 --------
    elif 3634889 <= patnum_int <= 3695820:
        for line in lines:
            if "[22]" in line:
                filed_date = extract_date_from_line(line)
            if "[45]" in line:
                patent_date = extract_date_from_line(line)

    # =================================================
    # GENERIC FALLBACK
    # =================================================

    if not patent_date or not filed_date:
        for line in lines:
            if not filed_date:
                if (
                    fuzzy_contains(line, "filed")
                    or fuzzy_contains(line, "application")
                    or "[22]" in line
                ):
                    filed_date = extract_date_from_line(line)

            if not patent_date:
                if (
                    fuzzy_contains(line, "patented")
                    or fuzzy_contains(line, "issued")
                    or "[45]" in line
                ):
                    patent_date = extract_date_from_line(line)

            if patent_date and filed_date:
                break

    # Sanity check
    if patent_date and filed_date:
        if parse(filed_date) > parse(patent_date):
            filed_date = ""

    return patent_date, filed_date


# =================================================
# REFERENCE LOADER
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
# VALIDATION LOGIC
# =================================================


def compare_dates_with_flags(extracted_row, reference_row):
    patnum_int = int(normalize_patnum(extracted_row["patnum"]))

    # -------------------------
    # Patent not in reference
    # -------------------------
    if not reference_row:
        return "Missing in reference", "Missing in reference", "Missing"

    # =================================================
    # EARLY PATENTS (NO FILING DATE)
    # =================================================
    if patnum_int < 137279:
        if (
            extracted_row["iyear"] == reference_row.get("iyear")
            and extracted_row["imonth"] == reference_row.get("imonth")
            and extracted_row["iday"] == reference_row.get("iday")
        ):
            return "No", "Missing in patent", "Correct"
        else:
            return "Yes", "Missing in patent", "Wrong"

    # =================================================
    # Missing reference dates
    # =================================================
    if not (
        reference_row.get("iyear")
        and reference_row.get("imonth")
        and reference_row.get("iday")
    ):
        return "Missing in reference", "Missing in reference", "Missing"

    if not (
        reference_row.get("fyear")
        and reference_row.get("fmonth")
        and reference_row.get("fday")
    ):
        return "Missing in reference", "Missing in reference", "Missing"

    # =================================================
    # Normal validation
    # =================================================

    patent_status = "No"
    if (
        extracted_row["iyear"] != reference_row["iyear"]
        or extracted_row["imonth"] != reference_row["imonth"]
        or extracted_row["iday"] != reference_row["iday"]
    ):
        patent_status = "Yes"

    filed_status = "No"
    if (
        extracted_row["fyear"] != reference_row["fyear"]
        or extracted_row["fmonth"] != reference_row["fmonth"]
        or extracted_row["fday"] != reference_row["fday"]
    ):
        filed_status = "Yes"

    if "Yes" in (patent_status, filed_status):
        flag = "Wrong"
    else:
        flag = "Correct"

    return patent_status, filed_status, flag


# =================================================
# MAIN
# =================================================


def run():
    extracted_rows = []

    for folder in sorted(os.listdir(OCR_ROOT)):
        folder_path = os.path.join(OCR_ROOT, folder)

        if not os.path.isdir(folder_path):
            continue

        first_page = get_first_text_file(folder_path)
        if not first_page:
            continue

        with open(
            os.path.join(folder_path, first_page),
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as f:
            text = f.read()

        patent_date, filed_date = extract_patent_dates(text, folder)

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
            f"[OK] {folder} | "
            f"Patent: {patent_date or 'N/A'} | "
            f"Filed: {filed_date or 'N/A'}"
        )

    reference_dict = load_csv_dict(REFERENCE_CSV)

    final_rows = []
    for row in extracted_rows:
        ref_row = reference_dict.get(normalize_patnum(row["patnum"]))
        pw, fw, flag = compare_dates_with_flags(row, ref_row)

        final_rows.append(
            {
                **row,
                "patent_wrong": pw,
                "filed_wrong": fw,
                "flag": flag,
            }
        )

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=final_rows[0].keys())
        writer.writeheader()
        writer.writerows(final_rows)

    print(f"\n✅ Done! Comparison CSV saved to:\n{OUTPUT_CSV}")


# =================================================
# ENTRY POINT
# =================================================

if __name__ == "__main__":
    run()

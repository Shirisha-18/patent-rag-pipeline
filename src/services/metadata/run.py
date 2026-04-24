"""
run.py
---------
Orchestration layer. Replaces the old run() function.

Responsibilities:
    1. Walk OCR folders, call extract_dates() per patent
    2. Compare extracted dates against reference CSV
    3. Apply anomaly detection (monotonicity, sanity checks)
    4. Write output CSV

Intentionally thin — no date parsing logic lives here.
"""

import csv
import os
from datetime import datetime
from typing import Optional

from dateparser import parse as dateparse
from dotenv import load_dotenv

from date2 import (
    Confidence,
    ExtractionResult,
    extract_dates,
)

load_dotenv()

# =============================================================================
# CONFIG
# =============================================================================

OCR_ROOT = os.getenv("OCR_ROOT")
REFERENCE_CSV = os.getenv("REFERENCE_CSV")
OUTPUT_CSV = os.path.join(
    os.getenv("OUTPUT_CSV_DIR"),
    f"patent_dates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
)

OUTPUT_FIELDS = [
    "patnum",
    "era",
    "iyear",
    "imonth",
    "iday",
    "fyear",
    "fmonth",
    "fday",
    "issue_confidence",
    "filing_confidence",
    "issue_date_comparison",
    "filing_date_comparison",
    "validation_result",
    "anomaly_flag",
]


# =============================================================================
# FILE HELPERS
# =============================================================================


def normalize_patnum(patnum) -> str:
    return str(patnum).lstrip("0")


def get_first_text_file(folder_path: str) -> Optional[str]:
    txt_files = [f for f in os.listdir(folder_path) if f.endswith("_text.txt")]
    return sorted(txt_files)[0] if txt_files else None


def safe_date(year, month, day) -> Optional[datetime]:
    try:
        if year and month and day:
            return datetime(int(year), int(month), int(day))
    except (ValueError, TypeError):
        pass
    return None


# =============================================================================
# REFERENCE CSV
# =============================================================================


def load_reference(path: str) -> dict:
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            data[normalize_patnum(row["patnum"])] = row
    return data


# =============================================================================
# COMPARISON
# =============================================================================


def compare(result: ExtractionResult, ref: Optional[dict], patnum_int: int) -> tuple:
    """
    Returns (issue_status, filing_status, validation_result).

    Statuses:
        "Yes"                 — matches reference
        "No"                  — does not match
        "Missing in reference"— reference has no value to compare against
        "Missing in patent"   — structurally absent (Era A filing)
    """
    if not ref:
        return "Missing in reference", "Missing in reference", "Wrong"

    iy, im, id_, fy, fm, fd = result.to_parts()

    extracted_issue = (iy, im, id_)
    extracted_filing = (fy, fm, fd)

    ref_issue = (ref.get("iyear", ""), ref.get("imonth", ""), ref.get("iday", ""))
    ref_filing = (ref.get("fyear", ""), ref.get("fmonth", ""), ref.get("fday", ""))

    def has_value(t):
        return all(t)

    # Issue
    if not has_value(ref_issue):
        issue_status = "Missing in reference"
    elif extracted_issue == ref_issue:
        issue_status = "Yes"
    else:
        issue_status = "No"

    # Filing
    if result.filing_confidence == Confidence.MISSING:
        filing_status = "Missing in patent"
    elif not has_value(ref_filing):
        filing_status = "Missing in reference"
    elif extracted_filing == ref_filing:
        filing_status = "Yes"
    else:
        filing_status = "No"

    # Validation
    if issue_status == "Yes" and filing_status in (
        "Yes",
        "Missing in reference",
        "Missing in patent",
    ):
        validation = "Correct"
    else:
        validation = "Wrong"

    return issue_status, filing_status, validation


# =============================================================================
# ANOMALY DETECTION
# =============================================================================

EARLIEST_VALID_DATE = datetime(1836, 7, 13)  # first modern USPTO patent
FILING_ERA_START = datetime(1873, 4, 1)  # filing dates begin ~Era B


def detect_anomaly(
    result: ExtractionResult,
    patnum_int: int,
    previous_issue_dt: Optional[datetime],
) -> str:
    """
    Returns anomaly flag string or "OK".

    Checks (in priority order):
        1. issue = filing date (same day — almost always wrong)
        2. issue before previous issue (monotonicity violation)
        3. issue before historical floor
        4. issue before filing date
    """
    iy, im, id_, fy, fm, fd = result.to_parts()
    issue_dt = safe_date(iy, im, id_)
    filing_dt = safe_date(fy, fm, fd)

    if not issue_dt:
        return "OK"

    filing_absent = result.filing_confidence == Confidence.MISSING

    # 1. issue = filing
    if not filing_absent and filing_dt and issue_dt == filing_dt:
        return "issue = file"

    # 2. monotonicity
    if previous_issue_dt and issue_dt < previous_issue_dt:
        return "issue < previous issue"

    # 3. historical floor
    if issue_dt < EARLIEST_VALID_DATE:
        return "old patent"

    # 4. issue before filing
    if (
        not filing_absent
        and filing_dt
        and filing_dt >= FILING_ERA_START
        and issue_dt < filing_dt
    ):
        return "issue < file"

    return "OK"


# =============================================================================
# MAIN
# =============================================================================


def run():
    reference = load_reference(REFERENCE_CSV)
    rows = []
    previous_issue_dt = None

    for folder in sorted(
        os.listdir(OCR_ROOT),
        key=lambda x: int(normalize_patnum(x)) if normalize_patnum(x).isdigit() else 0,
    ):
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

        try:
            patnum_int = int(normalize_patnum(folder))
        except ValueError:
            print(f"[SKIP] {folder} — cannot parse patent number")
            continue

        result = extract_dates(text, patnum_int)
        iy, im, id_, fy, fm, fd = result.to_parts()

        ref = reference.get(normalize_patnum(folder))
        issue_comp, filing_comp, validation = compare(result, ref, patnum_int)
        anomaly = detect_anomaly(result, patnum_int, previous_issue_dt)

        # Advance monotonicity tracker
        issue_dt = safe_date(iy, im, id_)
        if issue_dt:
            previous_issue_dt = issue_dt

        rows.append(
            {
                "patnum": folder,
                "era": result.era,
                "iyear": iy,
                "imonth": im,
                "iday": id_,
                "fyear": fy,
                "fmonth": fm,
                "fday": fd,
                "issue_confidence": result.issue_confidence.value,
                "filing_confidence": result.filing_confidence.value,
                "issue_date_comparison": issue_comp,
                "filing_date_comparison": filing_comp,
                "validation_result": validation,
                "anomaly_flag": anomaly,
            }
        )

        print(
            f"[{result.era}] {folder} | "
            f"Issue: {result.issue_date or 'N/A'} ({result.issue_confidence.value}) | "
            f"Filed: {result.filing_date or 'N/A'} ({result.filing_confidence.value}) | "
            f"{validation} | {anomaly}"
        )

    if rows:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n✅ Done. {len(rows)} patents written to:\n{OUTPUT_CSV}")
    else:
        print("⚠️  No rows produced — check OCR_ROOT path.")


if __name__ == "__main__":
    run()

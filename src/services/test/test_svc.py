import os
import csv
from dotenv import load_dotenv

load_dotenv()

REFERENCE_CSV = os.getenv("REFERENCE_CSV")
OUTPUT_CSV_DIR = os.getenv("OUTPUT_CSV_DIR")

TARGET_PATNUMS = ["2402069", "3656046", "2932861", "3249178"]


def normalize_patnum(patnum):
    return str(patnum).lstrip("0")


def normalize_date_field(val):
    if val is None:
        return ""
    return str(val).strip().lstrip("0") or "0"


def load_csv_dict(path):
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = normalize_patnum(row["patnum"])
            data[key] = row
    return data


def load_output_csv(path):
    rows = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = normalize_patnum(row["patnum"])
            rows[key] = row
    return rows


# =================================================
# PICK OUTPUT CSV
# =================================================
available = sorted(
    [
        f
        for f in os.listdir(OUTPUT_CSV_DIR)
        if f.startswith("patent_dates_comparison") and f.endswith(".csv")
    ],
    reverse=True,
)

print("Available output CSVs:")
for i, f in enumerate(available):
    print(f"  [{i}] {f}")

choice = input("\nEnter number (Enter = most recent): ").strip()
chosen_file = available[int(choice) if choice.isdigit() else 0]
LATEST_OUTPUT_CSV = os.path.join(OUTPUT_CSV_DIR, chosen_file)
print(f"Using: {chosen_file}\n")

# =================================================
# LOAD
# =================================================
reference_dict = load_csv_dict(REFERENCE_CSV)
output_rows = load_output_csv(LATEST_OUTPUT_CSV)

# =================================================
# INSPECT EACH TARGET PATENT
# =================================================
for patnum in TARGET_PATNUMS:
    key = normalize_patnum(patnum)
    ref = reference_dict.get(key)
    out = output_rows.get(key)

    print("=" * 60)
    print(f"Patent {patnum}")
    print("=" * 60)

    if not ref:
        print("  NOT FOUND in reference CSV\n")
        continue
    if not out:
        print("  NOT FOUND in output CSV\n")
        continue

    # Print all reference columns so we can see the full schema
    print("  REFERENCE (all columns):")
    for col, val in ref.items():
        print(f"    {col:12s} = {val!r}")

    print()
    print("  EXTRACTED OUTPUT (all columns):")
    for col, val in out.items():
        print(f"    {col:12s} = {val!r}")

    print()
    print("  ISSUE DATE comparison:")
    for ext_f, ref_f in [("iyear", "iyear"), ("imonth", "imonth"), ("iday", "iday")]:
        ev = out.get(ext_f, "")
        rv = ref.get(ref_f, "")
        raw = "✅" if ev == rv else "❌"
        norm = "✅" if normalize_date_field(ev) == normalize_date_field(rv) else "❌"
        print(f"    {ext_f}: ext={ev!r:8s} ref={rv!r:8s}  raw={raw}  norm={norm}")

    print()
    print("  FILING DATE comparison:")
    for ext_f, ref_f in [("fyear", "fyear"), ("fmonth", "fmonth"), ("fday", "fday")]:
        ev = out.get(ext_f, "")
        rv = ref.get(ref_f, "")
        raw = "✅" if ev == rv else "❌"
        norm = "✅" if normalize_date_field(ev) == normalize_date_field(rv) else "❌"
        print(f"    {ext_f}: ext={ev!r:8s} ref={rv!r:8s}  raw={raw}  norm={norm}")

    print()
    print(f"  issue_date_comparison  = {out.get('issue_date_comparison')}")
    print(f"  filing_date_comparison = {out.get('filing_date_comparison')}")
    print(f"  validation_result      = {out.get('validation_result')}")
    print(f"  anomaly_flag           = {out.get('anomaly_flag')}")
    print()

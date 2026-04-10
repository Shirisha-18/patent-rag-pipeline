import os
import csv
from dotenv import load_dotenv

load_dotenv()

REFERENCE_CSV = os.getenv("REFERENCE_CSV")
OUTPUT_CSV = os.getenv("OUTPUT_CSV_DIR")  # point this to your latest output CSV

# =================================================
# Update this to your latest output CSV path
# =================================================
LATEST_OUTPUT_CSV = os.path.join(
    OUTPUT_CSV,
    input(
        "Enter your output CSV filename (e.g. patent_dates_comparison_20240101_120000.csv): "
    ).strip(),
)

EARLY_PATENT_NUM = 137279


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
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# =================================================
# LOAD DATA
# =================================================
print("\nLoading reference CSV...")
reference_dict = load_csv_dict(REFERENCE_CSV)

print("Loading output CSV...")
output_rows = load_output_csv(LATEST_OUTPUT_CSV)

print(f"  Reference rows : {len(reference_dict)}")
print(f"  Output rows    : {len(output_rows)}")


# =================================================
# TEST 1: Raw field value inspection
# Prints exact bytes for each field to reveal
# whitespace, leading zeros, type differences
# =================================================
print("\n" + "=" * 70)
print("TEST 1: RAW FIELD VALUES — shows exact content including whitespace")
print("=" * 70)

INSPECT_PATNUMS = input(
    "\nEnter comma-separated patnums to inspect (or press Enter to skip): "
).strip()

if INSPECT_PATNUMS:
    for patnum in [p.strip() for p in INSPECT_PATNUMS.split(",")]:
        key = normalize_patnum(patnum)
        ref = reference_dict.get(key)
        out = next(
            (r for r in output_rows if normalize_patnum(r["patnum"]) == key), None
        )

        print(f"\n--- Patent {patnum} ---")
        if not ref:
            print("  NOT FOUND in reference CSV")
        else:
            print("  REFERENCE:")
            for field in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]:
                val = ref.get(field)
                print(
                    f"    {field:8s} = [{val}]  type={type(val).__name__}  repr={repr(val)}"
                )

        if not out:
            print("  NOT FOUND in output CSV")
        else:
            print("  EXTRACTED (output CSV):")
            for field in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]:
                val = out.get(field)
                print(
                    f"    {field:8s} = [{val}]  type={type(val).__name__}  repr={repr(val)}"
                )

        if ref and out:
            print("  FIELD-BY-FIELD COMPARISON (raw vs normalized):")
            for field in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]:
                rv = ref.get(field, "")
                ov = out.get(field, "")
                raw_match = rv == ov
                norm_match = normalize_date_field(rv) == normalize_date_field(ov)
                status = (
                    "✅ MATCH"
                    if raw_match
                    else ("⚠️  NORM_MATCH" if norm_match else "❌ MISMATCH")
                )
                print(f"    {field:8s}  ref=[{rv!r:12s}]  ext=[{ov!r:12s}]  {status}")


# =================================================
# TEST 2: Find all cases where output says "No" (mismatch)
# but raw reference values actually match extracted values
# after normalization — these are false negatives
# =================================================
print("\n" + "=" * 70)
print("TEST 2: FALSE NEGATIVES — 'No' in output but values actually match")
print("(These are caused by leading zeros / whitespace / type differences)")
print("=" * 70)

false_negatives = []

for row in output_rows:
    key = normalize_patnum(row["patnum"])
    ref = reference_dict.get(key)
    if not ref:
        continue

    # Check issue date
    if row.get("issue_date_comparison") == "No":
        norm_match = (
            normalize_date_field(row["iyear"]) == normalize_date_field(ref.get("iyear"))
            and normalize_date_field(row["imonth"])
            == normalize_date_field(ref.get("imonth"))
            and normalize_date_field(row["iday"])
            == normalize_date_field(ref.get("iday"))
        )
        if norm_match:
            false_negatives.append(
                {
                    "patnum": row["patnum"],
                    "field": "issue",
                    "ext_year": row["iyear"],
                    "ref_year": ref.get("iyear"),
                    "ext_month": row["imonth"],
                    "ref_month": ref.get("imonth"),
                    "ext_day": row["iday"],
                    "ref_day": ref.get("iday"),
                }
            )

    # Check filing date
    if row.get("filing_date_comparison") == "No":
        norm_match = (
            normalize_date_field(row["fyear"]) == normalize_date_field(ref.get("fyear"))
            and normalize_date_field(row["fmonth"])
            == normalize_date_field(ref.get("fmonth"))
            and normalize_date_field(row["fday"])
            == normalize_date_field(ref.get("fday"))
        )
        if norm_match:
            false_negatives.append(
                {
                    "patnum": row["patnum"],
                    "field": "filing",
                    "ext_year": row["fyear"],
                    "ref_year": ref.get("fyear"),
                    "ext_month": row["fmonth"],
                    "ref_month": ref.get("fmonth"),
                    "ext_day": row["fday"],
                    "ref_day": ref.get("fday"),
                }
            )

if false_negatives:
    print(f"\nFound {len(false_negatives)} false negative(s):\n")
    for fn in false_negatives:
        print(f"  Patent {fn['patnum']} [{fn['field']}]")
        print(f"    year  : ext=[{fn['ext_year']!r}]  ref=[{fn['ref_year']!r}]")
        print(f"    month : ext=[{fn['ext_month']!r}]  ref=[{fn['ref_month']!r}]")
        print(f"    day   : ext=[{fn['ext_day']!r}]  ref=[{fn['ref_day']!r}]")
else:
    print("\n  ✅ No false negatives found — comparisons look accurate.")


# =================================================
# TEST 3: Leading zero audit across entire reference CSV
# Shows how many reference fields have leading zeros
# so you know how widespread the issue is
# =================================================
print("\n" + "=" * 70)
print("TEST 3: LEADING ZERO AUDIT — reference CSV field format")
print("=" * 70)

leading_zero_counts = {
    f: 0 for f in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]
}
non_empty_counts = {
    f: 0 for f in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]
}

for ref in reference_dict.values():
    for field in leading_zero_counts:
        val = ref.get(field, "")
        if val and str(val).strip():
            non_empty_counts[field] += 1
            if str(val).strip().startswith("0"):
                leading_zero_counts[field] += 1

print(
    f"\n  {'Field':8s}  {'Has leading zero':>20s}  {'Total non-empty':>16s}  {'% affected':>12s}"
)
print(f"  {'-' * 8}  {'-' * 20}  {'-' * 16}  {'-' * 12}")
for field in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]:
    total = non_empty_counts[field]
    zeros = leading_zero_counts[field]
    pct = f"{zeros / total * 100:.1f}%" if total else "N/A"
    flag = " ⚠️" if zeros > 0 else " ✅"
    print(f"  {field:8s}  {zeros:>20d}  {total:>16d}  {pct:>12s}{flag}")


# =================================================
# TEST 4: Whitespace audit — check for trailing/leading spaces
# =================================================
print("\n" + "=" * 70)
print("TEST 4: WHITESPACE AUDIT — reference CSV fields with spaces")
print("=" * 70)

whitespace_found = False
for key, ref in reference_dict.items():
    for field in ["iyear", "imonth", "iday", "fyear", "fmonth", "fday"]:
        val = ref.get(field, "")
        if val and val != val.strip():
            print(f"  ⚠️  Patent {key} field [{field}] has whitespace: {val!r}")
            whitespace_found = True

if not whitespace_found:
    print("  ✅ No whitespace issues found in reference CSV.")


# =================================================
# SUMMARY
# =================================================
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
issue_no_count = sum(1 for r in output_rows if r.get("issue_date_comparison") == "No")
filing_no_count = sum(1 for r in output_rows if r.get("filing_date_comparison") == "No")
total_fn = len(false_negatives)
issue_fn_count = sum(1 for fn in false_negatives if fn["field"] == "issue")
filing_fn_count = sum(1 for fn in false_negatives if fn["field"] == "filing")

print(f"  Total 'No' in issue_date_comparison  : {issue_no_count}")
print(f"  Total 'No' in filing_date_comparison : {filing_no_count}")
print(
    f"  False negatives (norm fixes)         : {total_fn}  (issue={issue_fn_count}, filing={filing_fn_count})"
)
if total_fn > 0:
    print(f"\n  ⚠️  Apply normalize_date_field() fix to compare_dates_with_flags()")
    print(f"     to convert {total_fn} false mismatches to correct matches.")
else:
    print(f"\n  ✅ Comparison logic appears correct — mismatches are genuine.")
print()

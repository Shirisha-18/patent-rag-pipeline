# test_patent_dates.py

import re
from dateparser import parse

# ===============================
# Step 1: Define test cases
# ===============================
test_patents = {
    "1062464": """UNITED STATES PATENT OFFICE.
HAROLD SHEMWELL, OF ASHEVILLE, NORTH CAROLINA, ASSIGNOR TO AMERICAN
AUTOMATIC RAILWAY SWITCH COMPANY, A CORPORATION OF ALABAMA,
AUTOMATIC SWITCH.
998,644.
Specification of Letters Patent.
Application filed August 24, 1909, Serial No. 514,378.
To all whom it may concern:
Patented July 25, 1911.
Renewed July 26, 1910. Serial No. 573,845.
hereinafter more particularly described and""",
    "1062161": """UNITED STATES PATENT OFFICE.
1,062,161.
ISIDOR KITSEE, OF PHILADELPHIA, PENNSYLVANIA.
TELEPHONY.
Specification of Letters Patent.
Application filed March 16, 1907, Serial No. 362,714.
To all whom it may concern:
Be it known that I, ISIDOR KITSEE, citizen
of the United States, residing at Philadel-
phia, in the county of Philadelphia and
5 State of Pennsylvania, have invented cer-
tain new and useful Improvements in Tele-
phony, of which the following is a specifi-
cation.
My invention relates to an improvement
10 in telephony. Its object is, to provide means
whereby, with the aid of one single circuit,
one message may be transmitted from one
terminal of said circuit simultaneously with
the transmission of a message from the other
15 terminal of said circuit.
Patented May 20, 1913.
Renewed July 19, 1912. Serial No. 710,487.
85
""",
    "1063169": """UNITED STATES PATENT OFFICE.
ALLEN DE VILBISS, JR., OF TOLEDO, OHIO, ASSIGNOR TO TOLEDO SCALE COMPANY, OF
NEWARK, NEW JERSEY, A CORPORATION OF NEW JERSEY.
1,063,169.
SCALE.
Specification of Letters Patent.
Patented May 27, 1913.
Application filed May 23, 1904, Serial No. 203,396. Renewed September 23, 1912. Serial No. 721,925
To all whom it may concern:
""",
}


# ===============================
# Step 2: Define extraction function
# ===============================
def extract_issue_and_filing(text):
    text = text.replace("\n", " ")  # flatten text

    # Extract issue date (Patented)
    patented_match = re.search(
        r"Patented\s+([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})", text, re.I
    )
    issue_date = ""
    if patented_match:
        dt = parse(patented_match.group(1))
        if dt:
            issue_date = dt.strftime("%m/%d/%Y")

    # Extract filing date (Application filed)
    filed_match = re.search(
        r"Application filed\s+([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4})", text, re.I
    )
    filing_date = ""
    if filed_match:
        dt = parse(filed_match.group(1))
        if dt:
            filing_date = dt.strftime("%m/%d/%Y")

    return issue_date, filing_date


# ===============================
# Step 3: Test the extraction
# ===============================
for patnum, text in test_patents.items():
    issue, filed = extract_issue_and_filing(text)
    print(f"{patnum} -> Issue: {issue}, Filed: {filed}")

import os
import spacy
import csv
from pathlib import Path

# -----------------------------
# Paths
# -----------------------------
OCR_ROOT = r"C:\Users\shiri\Dropbox\ocr_patents\ocr_patents\random_sample"
MODEL_PATH = Path(__file__).parent / "patent_ner"
OUTPUT_FILE = Path(__file__).parent.parent / "output" / "final_patent_metadata.csv"

# -----------------------------
# Load trained SpaCy model
# -----------------------------
nlp = spacy.load(MODEL_PATH)


# -----------------------------
# Helper: extract text from all pages in a folder
# -----------------------------
def get_folder_text(folder_path):
    txt_files = [f for f in os.listdir(folder_path) if f.endswith("_text.txt")]
    if not txt_files:
        return "", ""
    txt_files.sort()
    all_text = ""
    for f in txt_files:
        with open(
            os.path.join(folder_path, f), "r", encoding="utf-8", errors="ignore"
        ) as file:
            all_text += file.read() + "\n"
    return txt_files[0], all_text  # return first page name + full text


# -----------------------------
# Extraction loop
# -----------------------------
rows = []
for folder in sorted(os.listdir(OCR_ROOT)):
    folder_path = os.path.join(OCR_ROOT, folder)
    if not os.path.isdir(folder_path):
        continue

    first_page, text = get_folder_text(folder_path)
    if not text.strip():
        continue

    doc = nlp(text)

    # Initialize row with empty values
    data = {
        "folder": folder,
        "first_page": first_page,
        "patent_number": "",
        "serial_number": "",
        "application_date": "",
        "patent_date": "",
        "inventors": "",
        "assignees": "",
        "title": "",
    }

    # Fill in extracted entities
    for ent in doc.ents:
        if ent.label_ in data:
            if ent.label_ in ["INVENTOR", "ASSIGNEE"]:
                # Concatenate multiple values
                if data[ent.label_]:
                    data[ent.label_] += ", " + ent.text
                else:
                    data[ent.label_] = ent.text
            else:
                data[ent.label_] = ent.text

    rows.append(data)

# -----------------------------
# Save CSV
# -----------------------------
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

print(f"Extraction complete. CSV saved at {OUTPUT_FILE}")

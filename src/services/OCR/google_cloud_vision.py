import os
import time
import csv
from datetime import datetime
from google.cloud import vision
import xml.etree.ElementTree as ET
from tqdm import tqdm
from contextlib import redirect_stderr
from io import StringIO

# ==================================================
# PATH & ENVIRONMENT CONFIGURATION
# ==================================================
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "src/vision_key.json"

SOURCE_ROOT = r"C:\Users\shiri\Dropbox\ocr_patents\patent_images_sample\random_sample"
OUTPUT_ROOT = r"C:\Users\shiri\Dropbox\ocr_patents\ocr_patents\random_sample"
LOG_DIR = r"C:\Users\shiri\Dropbox\ocr_patents\info"

os.makedirs(LOG_DIR, exist_ok=True)

# Timestamp-based log file names
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
DETAILED_LOG = os.path.join(LOG_DIR, f"detailed_log_{timestamp}.txt")
SUMMARY_LOG = os.path.join(LOG_DIR, f"summary_report_{timestamp}.txt")
RUN_HISTORY = os.path.join(LOG_DIR, "run_summary_history.csv")


# ==================================================
# TEXT DETECTION FUNCTION
# ==================================================
def detect_text(image_path):
    """Extract text from an image using Google Cloud Vision OCR."""
    client = vision.ImageAnnotatorClient()
    with open(image_path, "rb") as image_file:
        content = image_file.read()

    image = vision.Image(content=content)
    f = StringIO()
    with redirect_stderr(f):
        response = client.text_detection(image=image)

    if response.error.message:
        raise Exception(response.error.message)

    texts = response.text_annotations
    return texts[0].description if texts else ""


# ==================================================
# XML PAGE RANGE EXTRACTION
# ==================================================
def get_page_ranges(xml_path):
    """Extract page ranges (abstract, description, claims) from XML."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def extract_range(tag):
        elem = root.find(f".//{tag}")
        if elem is not None:
            begin = elem.find("begin")
            end = elem.find("end")
            if begin is not None and end is not None:
                return (int(begin.text), int(end.text))
        return None

    ranges = []
    for tag in ["abstract-pages", "description-pages", "claims-pages"]:
        r = extract_range(tag)
        if r:
            ranges.append(r)
    return ranges


# ==================================================
# COST CALCULATION
# ==================================================
def calculate_cost(total_pages_processed):
    """
    Calculate OCR cost for all processed pages.
    Every page is billed at $1.50 per 1000 units.
    No free-tier deduction.
    """
    COST_PER_1000 = 1.50
    return (total_pages_processed / 1000) * COST_PER_1000


# ==================================================
# MAIN EXECUTION
# ==================================================
def main():
    start_time = time.time()

    total_folders = 0
    total_pages_processed = 0
    total_skipped = 0
    total_failed = 0
    total_already_done = 0

    with open(DETAILED_LOG, "w", encoding="utf-8") as log_file:
        folders = [
            f
            for f in os.listdir(SOURCE_ROOT)
            if os.path.isdir(os.path.join(SOURCE_ROOT, f))
        ]

        for folder in tqdm(folders, desc="Processing Folders", unit="folder"):
            folder_path = os.path.join(SOURCE_ROOT, folder)
            xml_files = [
                f for f in os.listdir(folder_path) if f.lower().endswith(".xml")
            ]

            if not xml_files:
                total_skipped += 1
                log_file.write(f"[SKIPPED] Folder '{folder}' - no XML found\n")
                continue

            xml_path = os.path.join(folder_path, xml_files[0])
            page_ranges = get_page_ranges(xml_path)

            if not page_ranges:
                total_skipped += 1
                log_file.write(f"[SKIPPED] Folder '{folder}' - no page ranges found\n")
                continue

            out_folder = os.path.join(OUTPUT_ROOT, folder)
            os.makedirs(out_folder, exist_ok=True)

            all_pages = [
                page for start, end in page_ranges for page in range(start, end + 1)
            ]

            for page_num in tqdm(all_pages, desc=f"{folder}", leave=False, unit="page"):
                filename = f"{page_num:08d}.tif"
                image_path = os.path.join(folder_path, filename)
                out_file = os.path.join(out_folder, f"{page_num:08d}_text.txt")

                if os.path.exists(out_file):
                    total_already_done += 1
                    log_file.write(f"[SKIPPED] Already processed {folder}/{filename}\n")
                    continue

                if not os.path.exists(image_path):
                    total_skipped += 1
                    log_file.write(f"[MISSING] {folder}/{filename}\n")
                    continue

                try:
                    text = detect_text(image_path)
                    total_pages_processed += 1
                except Exception as e:
                    total_failed += 1
                    log_file.write(f"[FAILED] {folder}/{filename} - {str(e)}\n")
                    continue

                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(text)

                log_file.write(f"[SUCCESS] {folder}/{filename} -> {out_file}\n")

            total_folders += 1

    elapsed = time.time() - start_time

    # Historical tracking (for cumulative analysis)
    prev_total = 0
    if os.path.exists(RUN_HISTORY):
        with open(RUN_HISTORY, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            prev_total = sum(int(row["pages_extracted"]) for row in reader)

    grand_total = prev_total + total_pages_processed
    total_cost = calculate_cost(grand_total)

    summary_lines = [
        "\nSUMMARY REPORT",
        "=" * 50,
        f"{'Folders Processed:':25} {total_folders:>10}",
        f"{'Pages Extracted:':25} {total_pages_processed:>10}",
        f"{'Pages Skipped:':25} {total_skipped:>10}",
        f"{'Pages Already Done:':25} {total_already_done:>10}",
        f"{'Pages Failed OCR:':25} {total_failed:>10}",
        "-" * 50,
        f"{'Total Time (sec):':25} {elapsed:.2f}",
        f"{'Cumulative Pages:':25} {grand_total:>10}",
        f"{'Estimated OCR Cost (USD):':25} {total_cost:>10.4f}",
        "=" * 50,
        "\nExtraction Completed Successfully!\n",
    ]

    print("\n".join(summary_lines))

    with open(SUMMARY_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    # Append run summary to CSV history
    file_exists = os.path.exists(RUN_HISTORY)
    with open(RUN_HISTORY, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if not file_exists:
            writer.writerow(
                [
                    "timestamp",
                    "folders_processed",
                    "pages_extracted",
                    "pages_failed",
                    "pages_skipped",
                    "total_time_sec",
                    "total_cost_usd",
                ]
            )
        writer.writerow(
            [
                timestamp,
                total_folders,
                total_pages_processed,
                total_failed,
                total_skipped,
                round(elapsed, 2),
                round(total_cost, 4),
            ]
        )


if __name__ == "__main__":
    main()

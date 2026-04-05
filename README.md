# Patent Intelligence Pipeline for OCR-to-RAG Systems

 <!--- ![Python](https://img.shields.io/badge/python-3.12-blue) 
 ![Google Cloud](https://img.shields.io/badge/GCP-Vision_API-orange) 
 ![License](https://img.shields.io/badge/license-MIT-green) --->

> End-to-end pipeline that converts scanned patent TIFF images into structured, validated metadata and retrieval-ready knowledge for RAG and LLM applications.

## Overview

This project builds an end-to-end patent intelligence pipeline that converts raw scanned patent documents (TIFF/images) into structured, validated, and retrieval-ready data. It is designed as a foundational layer for Retrieval-Augmented Generation (RAG) and LLM-based applications.

The system goes beyond basic OCR by incorporating context-aware parsing, metadata extraction, and anomaly detection, enabling reliable downstream use in search, analytics, and AI systems.

## Background (USPTO Metadata)

USPTO patent documents contain structured bibliographic and legal metadata describing patent applications and grants. This includes identifiers, classification codes, filing information, and prosecution history.

Key components include:

1. Identifiers: Application number, patent number, publication number, attorney docket number  
2. Bibliographic Data: Title, inventors, assignee, applicant information  
3. Dates: Filing date, publication date, issue date, priority date  
4. Classification: CPC / USPC codes  
5. Prosecution History: Office actions, examiner details, continuity data  
6. Foreign Priority: International filings  
7. Correspondence Data: Attorney or agent of record  

These fields typically exist in structured XML formats in USPTO bulk datasets; however, this project does NOT use XML and instead derives similar structured information from OCR output.


## Methodology

### 1. Data Input
- Scanned patent documents in TIFF format

### 2. OCR Extraction
- Google Cloud Vision API is used to extract raw text from images
- Handles multi-column layouts and structured document regions

### 3. Information Extraction
- Extract structured metadata from OCR text using parsing and heuristics
- Identify:
  - Patent titles
  - Inventor names
  - Assignees
  - Classification codes (if present in text)
  - Claims and abstract segmentation

### 4. Output Representation
- Structured JSON-like patent representation generated from OCR output

## Project Structure

```
├── LICENSE              <- MIT License for open-source usage
├── README.md            <- Project documentation and usage guide
├── requirements.txt     <- Python dependencies for the OCR pipeline
├── .gitignore           <- Files and folders excluded from version control

├── src/
│   └── services/
│       ├── ocr/         <- Google Cloud Vision OCR extraction scripts
│       ├── metadata/    <- Parsing and structuring OCR-extracted text
│       ├── test/        <- Unit tests and validation scripts

```

## Environment Setup

**1. Create and activate Conda environment**

```
conda create -n gcp-cloud-vision python=3.12 -y
conda activate gcp-cloud-vision
```

**2. Install required packages**
```
Install required packages
```

## Google Cloud Vision Setup

1. Go to Google Cloud Console
2. Create a project and **enable Vision API**
3. Under **IAM & Admin → Service Accounts**, create a **service account key**
4. Download the **JSON key file**


## Add Images
Place all images in: `ocr-ai-engine/data/raw/`
Supported formats: `.jpg`, `.jpeg`, `.png`, `.tif`, `.bmp`

## Run the Code
From the **project root**, run:

```
conda activate gcp-cloud-vision
python src/google_cloud_vision.py
```

## Future Enhancements

1. Docker container for reproducibility
2. Integration with **LangChain / RAG** for text analysis
3. Store OCR results in database / JSON
4. Large-scale batch processing with **Google Cloud Storage**

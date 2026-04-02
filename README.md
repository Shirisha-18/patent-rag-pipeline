# OCR-AI-Engine

 <!--- ![Python](https://img.shields.io/badge/python-3.12-blue) 
 ![Google Cloud](https://img.shields.io/badge/GCP-Vision_API-orange) 
 ![License](https://img.shields.io/badge/license-MIT-green) --->

> Extract text from images and scanned documents using **Google Cloud Vision API**.  
> Designed as a foundation for OCR pipelines and future RAG / LLM systems.

## Overview

This project processes scanned patent documents (TIFF format) from USPTO and Google Patents datasets using OCR-based extraction.

The system converts unstructured image-based patent documents into structured, machine-readable representations suitable for downstream information retrieval and language model applications.

## Patent Domain Context (USPTO Metadata)

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

## 📁 Project Structure

```
├── LICENSE              <- MIT License for open-source usage
├── README.md            <- Project documentation and usage guide
├── requirements.txt     <- Python dependencies for the OCR pipeline
├── .gitignore           <- Files and folders excluded from version control

├── data/
│   ├── raw/             <- Input TIFF patent images (USPTO / Google Patents)
│   ├── processed/       <- OCR outputs and extracted structured data
│   ├── external/        <- (Optional) external reference datasets if used
│   ├── interim/         <- Intermediate OCR outputs before final processing

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


<!--- 
## 📁 Project Structure

```
├── LICENSE            <- MIT License
├── README.md          <- The top-level README for developers using this project
├── data
│   ├── external       <- Data from third party sources
│   ├── interim        <- Intermediate data that has been transformed
│   ├── processed      <- The final, canonical data sets for modeling
│   └── raw            <- The original, immutable data dump
│
├── models             <- Trained and serialized models, model predictions, or model summaries
│
├── notebooks          <- Jupyter notebooks. Naming convention is a number (for ordering),
│                         the creator's initials, and a short `-` delimited description, e.g.
│                         `1.0-jqp-initial-data-exploration`
│
├── references         <- Data dictionaries, manuals, and all other explanatory materials
│
├── reports            <- Generated analysis as HTML, PDF, LaTeX, etc.
│   └── figures        <- Generated graphics and figures to be used in reporting
│
├── requirements.txt   <- The requirements file for reproducing the analysis environment, e.g.
│                         generated with `pip freeze > requirements.txt`
│
└── src                         <- Source code for this project
    │
    ├── __init__.py             <- Makes src a Python module
    │
    ├── config.py               <- Store useful variables and configuration
    │
    ├── dataset.py              <- Scripts to download or generate data
    │
    ├── features.py             <- Code to create features for modeling
    │
    │    
    ├── modeling                
    │   ├── __init__.py 
    │   ├── predict.py          <- Code to run model inference with trained models          
    │   └── train.py            <- Code to train models
    │
    ├── plots.py                <- Code to create visualizations 
    │
    └── services                <- Service classes to connect with external platforms, tools, or APIs
        └── __init__.py 
```

--->
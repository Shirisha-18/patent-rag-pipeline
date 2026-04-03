import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.services.ocr.google_cloud_vision import main as run_ocr
from src.services.metadata.extract_date import run as run_metadata

st.set_page_config(page_title="Patent OCR Pipeline", layout="centered")
st.title("🔬 Patent OCR Pipeline")
st.caption("USPTO patent image processing — Google Cloud Vision")

# Show current config so you can verify paths
with st.expander("📁 Current Dropbox paths (from .env)"):
    st.code(f"""
SOURCE_ROOT   : {os.getenv("SOURCE_ROOT")}
OUTPUT_ROOT   : {os.getenv("OUTPUT_ROOT")}
OCR_ROOT      : {os.getenv("OCR_ROOT")}
REFERENCE_CSV : {os.getenv("REFERENCE_CSV")}
OUTPUT_CSV_DIR: {os.getenv("OUTPUT_CSV_DIR")}
LOG_DIR       : {os.getenv("LOG_DIR")}
""")

st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("Step 1: OCR Extraction")
    st.write("Reads TIFF images from Dropbox, extracts text via Google Vision.")
    if st.button("▶ Run OCR", use_container_width=True, type="primary"):
        with st.spinner("Running OCR... this may take a while"):
            try:
                run_ocr()
                st.success("✅ OCR complete!")
            except Exception as e:
                st.error(f"❌ Failed: {e}")

with col2:
    st.subheader("Step 2: Extract Metadata")
    st.write("Reads OCR text files, extracts patent dates, saves comparison CSV.")
    if st.button("▶ Extract Metadata", use_container_width=True, type="primary"):
        with st.spinner("Extracting patent dates..."):
            try:
                run_metadata()
                st.success("✅ Metadata extracted!")
            except Exception as e:
                st.error(f"❌ Failed: {e}")

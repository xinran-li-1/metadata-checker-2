#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
from pathlib import Path

import streamlit as st
import pandas as pd

from readme_extractor import (
    pdf_to_text,
    normalize_text,
    ai_extract_metadata,
    extract_urls_from_text,
    compute_needs_review_for_record,
    summarize_sources,
    summarize_needs_review,
    summarize_years,
    MIN_VALID_YEAR,
    MAX_VALID_YEAR,
)

# Page configuration
st.set_page_config(page_title="README Metadata Checker", layout="wide")

# Gradient title
st.markdown(
    """
    <h1 style="
        text-align: center;
        font-family: 'Trebuchet MS','Helvetica Neue',Arial,sans-serif;
        font-weight: 800;
        font-size: 2.6rem;
        background: linear-gradient(90deg, #ff6cab, #7366ff);
        -webkit-background-clip: text;
        color: transparent;
        letter-spacing: 0.08em;
        margin-top: 0.2em;
        margin-bottom: 0.1em;
    ">
        README METADATA CHECKER
    </h1>
    <p style="
        text-align: center;
        color: #666666;
        font-size: 0.95rem;
        margin-top: 0;
        margin-bottom: 1.5em;
    ">
        Data sources · Automatic checks · Needs Review flagging
    </p>
    """,
    unsafe_allow_html=True,
)

# Instructions
with st.expander("How to use", expanded=False):
    st.markdown(
        """
        This dashboard helps you inspect data source information in README / report PDFs.

        **Workflow:**
        1. Choose the PDF source below (file upload and/or local folder path).  
        2. For each file, the app runs a baseline extraction (sources, years, downloadability).  
        3. It computes `needs_review` using rule-based logic.  
        4. Use the tabs above to view: Overview / Per-file details / Aggregate charts.  

        Typical reasons for `needs_review = True`:
        - No source names detected.  
        - Years far in the future (e.g. > 2025).  
        - URLs that look like software purchase pages, gift card pages, or other non-data sites.  
        - Too little information; extraction is uncertain.  

        These files are recommended for manual inspection.
        """
    )

# Sidebar filters
with st.sidebar:
    st.header("Filters")

    view_mode = st.radio(
        "File list view:",
        options=["All files", "Only Needs Review = True"],
        index=0,
    )

    st.markdown("---")
    st.caption("Upload and folder-based loading can be used together; results are merged.")


# Process a single PDF
def process_one_pdf(file_path: Path, display_name: str) -> dict:
    """
    Run the extraction pipeline on a single PDF and return a record dict.
    """
    text = normalize_text(pdf_to_text(str(file_path)))
    ai_meta = ai_extract_metadata(text)
    urls = extract_urls_from_text(text)

    record = {
        "pdf_name": display_name,
        "sources_mentions": ai_meta["sources_ai"],
        # Use full text as the time_mentions field (downstream code will parse years)
        "time_mentions": [text],
        "urls": urls,
        "dataset_candidates": [],
        "has_declaration": False,
        "availability_section_found": False,
    }
    record["needs_review"] = compute_needs_review_for_record(record)
    record["years_ai"] = ai_meta["years_ai"]
    record["has_downloadable_data_ai"] = ai_meta["has_downloadable_data_ai"]
    return record


# Step 1: choose PDF source
st.markdown("### Step 1: Select README / report PDFs")

col_left, col_right = st.columns(2)

# Upload PDFs
with col_left:
    st.markdown("**Option A: Upload one or more PDFs**")
    uploaded_files = st.file_uploader(
        "Drag and drop or select PDF files:",
        type=["pdf"],
        accept_multiple_files=True,
        key="uploader_pdf_files",
    )

# Local folder path
with col_right:
    st.markdown("**Option B: Read all PDFs from a local folder**")
    default_path = str(Path.home())
    folder_str = st.text_input(
        "Local folder path (for example `/Users/admin/Desktop/readmes`):",
        value=default_path,
        key="local_folder_path",
    )
    folder_button = st.button("Load all PDFs from this folder", type="primary")

# Session state for folder-based records
if "folder_records" not in st.session_state:
    st.session_state["folder_records"] = []
if "folder_info_msg" not in st.session_state:
    st.session_state["folder_info_msg"] = ""

# Load from folder
if folder_button:
    folder_path = Path(folder_str).expanduser()
    if not folder_path.exists() or not folder_path.is_dir():
        st.session_state["folder_records"] = []
        st.session_state["folder_info_msg"] = "Path does not exist or is not a directory. Please check and try again."
    else:
        pdf_paths = sorted(folder_path.rglob("*.pdf"))
        if not pdf_paths:
            st.session_state["folder_records"] = []
            st.session_state["folder_info_msg"] = "No `.pdf` files found in this folder."
        else:
            folder_records = []
            progress_bar = st.progress(0.0, text="Reading PDFs from folder …")
            for idx, p in enumerate(pdf_paths, start=1):
                folder_records.append(process_one_pdf(p, p.name))
                progress_bar.progress(idx / len(pdf_paths))
            st.session_state["folder_records"] = folder_records
            st.session_state["folder_info_msg"] = (
                f"Loaded and parsed {len(pdf_paths)} PDF files from the folder."
            )

# Folder info message
if st.session_state["folder_info_msg"]:
    st.info(st.session_state["folder_info_msg"])


# Merge records from upload and folder
records: list[dict] = []

upload_records: list[dict] = []
if uploaded_files:
    progress_bar = st.progress(0.0, text="Parsing uploaded PDFs …")
    for idx, uf in enumerate(uploaded_files, start=1):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uf.read())
            tmp_path = Path(tmp.name)
        upload_records.append(process_one_pdf(tmp_path, uf.name))
        progress_bar.progress(idx / len(uploaded_files))

folder_records = st.session_state["folder_records"]

records.extend(upload_records)
records.extend(folder_records)

n_upload = len(upload_records)
n_folder = len(folder_records)

# If we have records, show results
if records:
    st.markdown(
        f"Currently parsed **{len(records)}** PDF files "
        f"(uploaded: {n_upload}, from folder: {n_folder})."
    )

    nr_stats = summarize_needs_review(records)
    n_files = len(records)
    n_need_true = nr_stats.get("True", 0)
    n_need_false = nr_stats.get("False", 0)

    flat_rows = []
    for r in records:
        flat_rows.append(
            {
                "pdf_name": r["pdf_name"],
                "sources": "; ".join(r["sources_mentions"]),
                "years_ai": "; ".join(str(y) for y in r["years_ai"]),
                "urls": "; ".join(r["urls"]),
                "has_downloadable_data_ai": r["has_downloadable_data_ai"],
                "needs_review": r["needs_review"],
            }
        )
    result_df = pd.DataFrame(flat_rows)

    st.markdown("### Step 2: Analysis views")
    tab_overview, tab_files, tab_charts = st.tabs(
        ["Overview", "Per-file details", "Aggregate charts"]
    )

    # Tab 1: Overview
    with tab_overview:
        st.markdown("#### Summary cards")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Total number of files", n_files)
        with col_b:
            st.metric("Files with Needs Review = True", n_need_true)
        with col_c:
            st.metric("Files with Needs Review = False", n_need_false)

        st.markdown("#### Export results")
        st.download_button(
            label="Download current results as CSV",
            data=result_df.to_csv(index=False).encode("utf-8"),
            file_name="readme_metadata_results.csv",
            mime="text/csv",
        )

        st.markdown("#### Preview table (first 50 rows)")
        st.dataframe(result_df.head(50), use_container_width=True)

    # Tab 2: Per-file details
    with tab_files:
        st.markdown("#### File-level inspection")

        if view_mode == "Only Needs Review = True":
            display_records = [r for r in records if r["needs_review"]]
            if not display_records:
                st.info("There are currently no files with `needs_review = True`.")
            else:
                st.caption(f"Showing {len(display_records)} files marked as Needs Review.")
        else:
            display_records = records
            st.caption(f"Showing all {len(display_records)} files.")

        for r in display_records:
            header = f"{r['pdf_name']}  "
            if r["needs_review"]:
                header += "(Needs Review)"
            else:
                header += "(Extraction looks OK)"

            with st.expander(header, expanded=False):
                st.write("**Sources (AI, normalized):**", r["sources_mentions"] or "(none detected)")
                st.write("**Years (AI):**", r["years_ai"] or "(none detected)")
                st.write("**URLs:**", r["urls"] or "(none detected)")
                st.write("**Has downloadable data (AI):**", r["has_downloadable_data_ai"])
                st.write("**Needs review?**", r["needs_review"])

    # Tab 3: Aggregate charts
    with tab_charts:
        st.markdown("#### Aggregate charts")

        col1, col2, col3 = st.columns(3)

        # Source distribution
        with col1:
            st.markdown("**Top sources (normalized)**")
            src_counter = summarize_sources(records)
            if src_counter:
                src_df = (
                    pd.DataFrame(
                        {
                            "source": list(src_counter.keys()),
                            "count": list(src_counter.values()),
                        }
                    )
                    .sort_values("count", ascending=False)
                )
                st.bar_chart(src_df.set_index("source"))
            else:
                st.write("(No source names detected in current files.)")

        # Needs review distribution
        with col2:
            st.markdown("**Needs Review counts (rule-based)**")
            nr_df = pd.DataFrame(
                {
                    "needs_review": list(nr_stats.keys()),
                    "count": list(nr_stats.values()),
                }
            )
            st.bar_chart(nr_df.set_index("needs_review"))

        # Year distribution
        with col3:
            st.markdown(f"**Year distribution ({MIN_VALID_YEAR}–{MAX_VALID_YEAR})**")
            year_counter = summarize_years(records)
            if year_counter:
                year_df = (
                    pd.DataFrame(
                        {
                            "year": list(year_counter.keys()),
                            "count": list(year_counter.values()),
                        }
                    )
                    .sort_values("year")
                    .set_index("year")
                )
                st.bar_chart(year_df)
            else:
                st.write("(No valid years detected in the specified range.)")

else:
    st.info("Please provide some files via either the upload area or the local folder option above.")

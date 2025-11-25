#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
readme_extractor.py — Phase 1++

Core utilities for README / PDF metadata extraction, cleaning, and summary:

- PDF to text: pdf_to_text
- Text normalization: normalize_text
- Heuristic "AI-style" extraction: ai_extract_metadata
- Source name normalization: normalize_source_name
- URL extraction: extract_urls_from_text
- Rule-based flagging: compute_needs_review_for_record
- Aggregation helpers: summarize_sources, summarize_needs_review, summarize_years
- Batch export helper: parse_pdfs_to_csv
- Optional visualization: save_visualizations(records, out_dir)

These functions can be imported and reused by other scripts or dashboards.
"""

import argparse
import json
import re
import sys
import random
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter

# Global year range for cleaning and visualization
MIN_VALID_YEAR = 1990
MAX_VALID_YEAR = 2025

# Domains that should not be treated as data sources
PSEUDO_DATA_DOMAINS = {
    "stata.com",
    "www.stata.com",
    "documents.worldbank.org",
    "www.documents.worldbank.org",
    "creativecommons.org",
    "www.creativecommons.org",
}


def is_pseudo_data_domain(domain: str) -> bool:
    """
    Return True if the domain should not be treated as a data source.
    """
    d = (domain or "").lower()
    if d in PSEUDO_DATA_DOMAINS:
        return True
    # Treat any domain containing "stata" as software-related
    if "stata" in d:
        return True
    return False


def is_pseudo_data_url(url: str) -> bool:
    """
    Return True if the URL clearly points to a non-data page (e.g. gift card).
    """
    u = (url or "").lower()
    if "gift card" in u or "giftcard" in u or "gift-card" in u:
        return True
    return False


# Source name cleaning and normalization

# Names that are clearly not data sources (lowercase)
PSEUDO_SOURCE_NAMES = {
    "creative commons",
    "documents of the world bank",
    "document of the world bank",
    "documents of world bank",
    "document of world bank",
    "documents of the world bank group",
}

# Variants -> canonical names (all lowercase)
SOURCE_NAME_MAP = {
    # World Bank variants
    "world bank": "world bank",
    "the world bank": "world bank",
    "world bank group": "world bank",
    "world  bank": "world bank",
    # WDI
    "wdi": "world development indicators",
    "world development indicators": "world development indicators",
    "world development  indicators": "world development indicators",
    "world development indicators (wdi)": "world development indicators",
    # PIP
    "pip": "poverty and inequality platform",
    "poverty and inequality platform": "poverty and inequality platform",
    # IMF
    "imf": "international monetary fund",
    "i.m.f.": "international monetary fund",
    "international monetary fund": "international monetary fund",
}


def normalize_source_name(raw: Any) -> Optional[str]:
    """
    Clean and normalize a source name.

    Steps:
    - Replace line breaks with spaces
    - Collapse all whitespace to a single space
    - Strip leading/trailing spaces
    - Lowercase
    - Drop known non-source names (e.g. Creative Commons)
    - Map known variants to canonical names

    Return canonical lowercase name, or None if it should be discarded.
    """
    if raw is None:
        return None

    s = str(raw)

    # Normalize line breaks
    s = s.replace("\r", "\n")
    # Collapse all whitespace (including \n, \t) to single space
    s = re.sub(r"\s+", " ", s)
    # Strip
    s = s.strip()
    if not s:
        return None

    s_lower = s.lower()

    # Drop obvious non-source labels
    if s_lower in PSEUDO_SOURCE_NAMES:
        return None
    if "creative commons" in s_lower:
        return None
    if "documents of the world bank" in s_lower:
        return None

    # Map to canonical forms when possible
    if s_lower in SOURCE_NAME_MAP:
        return SOURCE_NAME_MAP[s_lower]

    # Otherwise return cleaned lowercase name
    return s_lower


# PDF to text

def _pdfminer_extract(path: str) -> str:
    """
    Extract text using pdfminer.six. Return empty string on failure.
    """
    extract_text = None
    try:
        from pdfminer.high_level import extract_text as _et
        extract_text = _et
    except Exception:
        return ""

    try:
        return extract_text(path) if extract_text is not None else ""
    except Exception:
        return ""


def _pypdf_extract(path: str) -> str:
    """
    Fallback text extraction using PyPDF2.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(path)
        out = []
        for p in reader.pages:
            try:
                out.append(p.extract_text() or "")
            except Exception:
                pass
        return "\n".join(out)
    except Exception:
        return ""


def pdf_to_text(path: str) -> str:
    """
    Extract text from a PDF, trying pdfminer first and PyPDF2 as fallback.
    """
    txt = _pdfminer_extract(path)
    if not txt or len(txt.strip()) < 60:
        txt = _pypdf_extract(path)
    return txt or ""


# Basic text normalization

def normalize_text(t: str) -> str:
    """
    Normalize line breaks, hyphenation, dashes, and excessive empty lines.
    """
    t = t.replace("\r", "\n")
    t = re.sub(r"-\n", "", t)
    t = re.sub(r"\u2013|\u2014|\u2212", "-", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


# AI-style heuristic extraction (placeholder for future LLM / NER)

def ai_extract_metadata(text: str) -> Dict[str, Any]:
    """
    Heuristic "AI-style" extraction from normalized text.

    Returns:
        - sources_ai: List[str] of normalized source names
        - years_ai: List[int] of all 19xx / 20xx years mentioned
        - has_downloadable_data_ai: bool flag for downloadability phrases
        - notes_ai: simple note about method
    """
    if not text:
        return {
            "sources_ai": [],
            "years_ai": [],
            "has_downloadable_data_ai": False,
            "notes_ai": "empty text",
        }

    t = text.lower()

    # Source detection via keyword list + normalization
    possible_source_phrases = [
        "world bank",
        "world bank group",
        "wdi",
        "world development indicators",
        "pip",
        "poverty and inequality platform",
        "imf",
        "international monetary fund",
        "world health organization",
        "who",
        "unicef",
        "unesco",
    ]

    sources_found: List[str] = []
    for phrase in possible_source_phrases:
        if phrase in t:
            norm = normalize_source_name(phrase)
            if norm and norm not in sources_found:
                sources_found.append(norm)

    # Year detection (19xx / 20xx)
    year_candidates: List[int] = []
    for y_str in re.findall(r"\b((?:19|20)\d{2})\b", t):
        try:
            year_candidates.append(int(y_str))
        except ValueError:
            continue
    years_unique = sorted(set(year_candidates))

    # Downloadability detection via simple keyword search
    download_keywords = [
        "download the data",
        "data can be downloaded",
        "available for download",
        "data are available at",
        "data is available at",
        "dataset is available at",
        "this data is available at",
        "publicly available data",
        "can be accessed at",
        "available online at",
    ]
    has_downloadable_data = any(kw in t for kw in download_keywords)

    notes = "rule-based extraction; replace with LLM/NER in the future"

    return {
        "sources_ai": sources_found,
        "years_ai": years_unique,
        "has_downloadable_data_ai": bool(has_downloadable_data),
        "notes_ai": notes,
    }


# Generic helpers and rules

def extract_urls_from_text(text: str) -> List[str]:
    """
    Extract all HTTP/HTTPS URLs using a simple regex.
    """
    if not text:
        return []
    return re.findall(r"https?://[^\s)\"'>]+", text)


def _domain_of(url: str) -> str:
    """
    Return the lowercase netloc/domain for a URL.
    """
    from urllib.parse import urlparse
    try:
        netloc = urlparse(url).netloc or ""
        return netloc.lower()
    except Exception:
        return ""


def compute_needs_review_for_record(record: Dict[str, Any]) -> bool:
    """
    Compute needs_review flag for a single record.

    Rules:
      1) No normalized sources detected
      2) No URLs and no dataset_candidates
      3) Any year > MAX_VALID_YEAR mentioned
      4) URLs hit pseudo-data domains or gift card patterns
    """
    # Normalize sources
    normalized_sources: List[str] = []
    for s in (record.get("sources_mentions") or []):
        norm = normalize_source_name(s)
        if norm:
            normalized_sources.append(norm)

    urls = record.get("urls") or []
    datasets = record.get("dataset_candidates") or []
    time_mentions = record.get("time_mentions") or []

    # Check for future years
    has_future_year = False
    for t in time_mentions:
        for y_str in re.findall(r"\b((?:19|20)\d{2})\b", str(t)):
            try:
                y_int = int(y_str)
            except ValueError:
                continue
            if y_int > MAX_VALID_YEAR:
                has_future_year = True

    # Check suspicious URLs/domains
    has_suspicious_domain = False
    for u in urls:
        if is_pseudo_data_url(u):
            has_suspicious_domain = True
            continue
        d = _domain_of(u)
        if d and is_pseudo_data_domain(d):
            has_suspicious_domain = True

    # Combine rules
    computed_needs_review = False
    if not normalized_sources:
        computed_needs_review = True
    if (not urls) and (not datasets):
        computed_needs_review = True
    if has_future_year:
        computed_needs_review = True
    if has_suspicious_domain:
        computed_needs_review = True

    return computed_needs_review


def summarize_sources(records: List[Dict[str, Any]]) -> Counter:
    """
    Count normalized sources over all records.
    """
    c = Counter()
    for r in records:
        for s in (r.get("sources_mentions") or []):
            norm = normalize_source_name(s)
            if norm:
                c[norm] += 1
    return c


def summarize_needs_review(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Recompute needs_review for each record and return counts for True/False.
    """
    true_count = 0
    for r in records:
        if compute_needs_review_for_record(r):
            true_count += 1
    return {
        "True": true_count,
        "False": max(0, len(records) - true_count),
    }


def summarize_years(records: List[Dict[str, Any]]) -> Counter:
    """
    Extract years from time_mentions and count those within the valid range.
    """
    year_counter = Counter()
    for r in records:
        for t in (r.get("time_mentions") or []):
            for y_str in re.findall(r"\b((?:19|20)\d{2})\b", str(t)):
                try:
                    y_int = int(y_str)
                except ValueError:
                    continue
                if MIN_VALID_YEAR <= y_int <= MAX_VALID_YEAR:
                    year_counter[y_int] += 1
    return year_counter


# Sampling and visualization

def select_sample(paths: List[Path], max_samples: int, mode: str, seed: int) -> List[Path]:
    """
    Subsample a list of paths by first N or random selection.
    """
    if max_samples is None or max_samples <= 0 or max_samples >= len(paths):
        return paths
    if mode == "first":
        return paths[:max_samples]
    rnd = random.Random(seed)
    return rnd.sample(paths, k=max_samples)


def _try_import_matplotlib():
    """
    Import matplotlib in a safe way; return None if unavailable.
    """
    try:
        import matplotlib.pyplot as plt
        return plt
    except Exception:
        return None


def save_visualizations(records: List[Dict[str, Any]], out_dir: Path, topk: int = 20) -> None:
    """
    Create PNG charts summarizing records (sources, datasets, domains, years, flags).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    plt = _try_import_matplotlib()
    if plt is None:
        print("[!] matplotlib not installed. Please run `pip install matplotlib`", file=sys.stderr)
        return

    source_counter = Counter()
    dataset_counter = Counter()
    domain_counter = Counter()
    url_count_list = []
    year_list = []
    future_year_counter = Counter()
    needs_review_counter = Counter()
    has_decl_counter = Counter()
    has_avail_counter = Counter()

    for r in records:
        # Sources (normalized)
        normalized_sources: List[str] = []
        for s in (r.get("sources_mentions") or []):
            norm = normalize_source_name(s)
            if norm:
                source_counter[norm] += 1
                normalized_sources.append(norm)

        # Datasets (raw)
        datasets = (r.get("dataset_candidates") or [])
        for ds in datasets:
            if ds:
                dataset_counter[ds] += 1

        # URLs and domains (with filters)
        urls = r.get("urls") or []
        url_count_list.append(len(urls))
        has_suspicious_domain = False
        for u in urls:
            # URL-level pseudo filter
            if is_pseudo_data_url(u):
                has_suspicious_domain = True
                continue
            d = _domain_of(u)
            if d:
                if is_pseudo_data_domain(d):
                    has_suspicious_domain = True
                else:
                    domain_counter[d] += 1

        # Years and future-year flags
        times = r.get("time_mentions") or []
        has_future_year = False
        for t in times:
            for y_str in re.findall(r"\b((?:19|20)\d{2})\b", str(t)):
                try:
                    y_int = int(y_str)
                except ValueError:
                    continue
                if y_int > MAX_VALID_YEAR:
                    has_future_year = True
                    future_year_counter[y_int] += 1
                    continue
                if MIN_VALID_YEAR <= y_int <= MAX_VALID_YEAR:
                    year_list.append(y_int)

        # Recompute needs_review with the same rules
        computed_needs_review = False
        if not normalized_sources:
            computed_needs_review = True
        if (not urls) and (not datasets):
            computed_needs_review = True
        if has_future_year:
            computed_needs_review = True
        if has_suspicious_domain:
            computed_needs_review = True

        needs_review_counter["True" if computed_needs_review else "False"] += 1
        has_decl_counter[str(bool(r.get("has_declaration")))] += 1
        has_avail_counter[str(bool(r.get("availability_section_found")))] += 1

    # Chart 1: Top sources
    if source_counter:
        top_s = source_counter.most_common(topk)
        labels, vals = zip(*top_s)
        plt.figure()
        plt.barh(range(len(vals)), vals)
        plt.yticks(range(len(vals)), labels)
        plt.xlabel("Count")
        plt.title(f"Top {topk} Sources (normalized)")
        plt.tight_layout()
        plt.savefig(out_dir / "sources_top20.png", dpi=160)
        plt.close()

    # Chart 2: Top datasets
    if dataset_counter:
        top_d = dataset_counter.most_common(topk)
        labels, vals = zip(*top_d)
        plt.figure()
        plt.barh(range(len(vals)), vals)
        plt.yticks(range(len(vals)), labels)
        plt.xlabel("Count")
        plt.title(f"Top {topk} Dataset Candidates")
        plt.tight_layout()
        plt.savefig(out_dir / "datasets_top20.png", dpi=160)
        plt.close()

    # Chart 3: Top URL domains
    if domain_counter:
        top_dom = domain_counter.most_common(topk)
        labels, vals = zip(*top_dom)
        plt.figure()
        plt.barh(range(len(vals)), vals)
        plt.yticks(range(len(vals)), labels)
        plt.xlabel("Count")
        plt.title(f"Top {topk} URL Domains (filtered)")
        plt.tight_layout()
        plt.savefig(out_dir / "domains_top20.png", dpi=160)
        plt.close()

    # Chart 4: Histogram of URLs per file
    if url_count_list:
        plt.figure()
        plt.hist(
            url_count_list,
            bins=min(20, max(5, len(set(url_count_list))))
        )
        plt.xlabel("URLs per file")
        plt.ylabel("Frequency")
        plt.title("Distribution of URL Counts per PDF")
        plt.tight_layout()
        plt.savefig(out_dir / "urls_per_file_hist.png", dpi=160)
        plt.close()

    # Chart 5: Year histogram (valid range only)
    if year_list:
        plt.figure()
        plt.hist(
            year_list,
            bins=range(MIN_VALID_YEAR, MAX_VALID_YEAR + 2)
        )
        plt.xlabel("Year mentioned")
        plt.ylabel("Frequency")
        plt.title(f"Histogram of Mentioned Years ({MIN_VALID_YEAR}–{MAX_VALID_YEAR})")
        plt.tight_layout()
        plt.savefig(out_dir / "years_hist.png", dpi=160)
        plt.close()

    # Chart 6: needs_review bar chart
    if needs_review_counter:
        labels = list(needs_review_counter.keys())
        vals = [needs_review_counter[k] for k in labels]
        plt.figure()
        plt.bar(labels, vals)
        plt.xlabel("needs_review (computed)")
        plt.ylabel("Count")
        plt.title("Files Marked as Needs Review (Rule-based)")
        plt.tight_layout()
        plt.savefig(out_dir / "needs_review_bar.png", dpi=160)
        plt.close()


# Batch helper: parse PDF directory to CSV

def parse_pdfs_to_csv(
    input_dir: str,
    glob_pattern: str,
    out_csv: str,
    max_samples: Optional[int] = None,
    sample_mode: str = "first",
    seed: int = 42,
) -> None:
    """
    Minimal batch helper:

    - Walk a directory to find PDFs
    - Extract text and AI-style metadata
    - Compute needs_review using shared rules
    - Write a light CSV (for dashboard or further analysis)
    """
    from csv import DictWriter

    base = Path(input_dir)
    pdf_paths = sorted(base.rglob(glob_pattern))
    pdf_paths = select_sample(pdf_paths, max_samples or 0, sample_mode, seed)

    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "pdf_path",
            "sources_ai",
            "years_ai",
            "urls",
            "has_downloadable_data_ai",
            "needs_review",
        ]
        writer = DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for p in pdf_paths:
            text = normalize_text(pdf_to_text(str(p)))
            ai_meta = ai_extract_metadata(text)
            urls = extract_urls_from_text(text)

            # Temporary record so we can reuse the central needs_review logic
            temp_record = {
                "sources_mentions": ai_meta["sources_ai"],
                "time_mentions": [text],
                "urls": urls,
                "dataset_candidates": [],
                "has_declaration": False,
                "availability_section_found": False,
            }
            needs_review = compute_needs_review_for_record(temp_record)

            # Clip years to valid range
            years_clipped = [
                y for y in ai_meta["years_ai"]
                if MIN_VALID_YEAR <= y <= MAX_VALID_YEAR
            ]

            writer.writerow({
                "pdf_path": str(p),
                "sources_ai": "; ".join(ai_meta["sources_ai"]),
                "years_ai": "; ".join(str(y) for y in years_clipped),
                "urls": "; ".join(urls),
                "has_downloadable_data_ai": ai_meta["has_downloadable_data_ai"],
                "needs_review": needs_review,
            })

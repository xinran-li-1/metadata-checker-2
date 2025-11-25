#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
download_readmes.py
One-click crawler for README files from the World Bank reproducibility portal.

Main features:
- Prioritize direct download lia'dnks; if insufficient, automatically scrape /catalog/?page=N pages.
- Crawl both catalog entries and their related-materials subpages.
- Check the download folder for existing README files (using catalog ID deduplication).
- Skip already-downloaded entries and automatically fetch more until reaching the LIMIT count.
"""

from pathlib import Path
from urllib.parse import urlparse, urljoin
import re, time, sys

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from tqdm import tqdm

# ========= Configurable Parameters =========
LIMIT = 200
OUT_DIR = Path("data/readmes")
BASE = "https://reproducibility.worldbank.org"
CATALOG_ROOT = f"{BASE}/catalog/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (WorldBank-README-Downloader/2.1)",
    "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
}
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 120
SLEEP_BETWEEN = 0.25

# ========= Direct download seed =========
SEED_README_URLS = [
    "https://reproducibility.worldbank.org/index.php/catalog/222/download/643/README.pdf",
]

# ========= Regex patterns (handle both with/without index.php) =========
RE_ITEM = re.compile(r"(?:/index\.php)?/catalog/\d+/?$")
RE_DOWNLOAD = re.compile(r"(?:/index\.php)?/catalog/\d+/download/\d+(?:/README\.pdf)?$", re.IGNORECASE)

def build_session() -> requests.Session:
    """Create a resilient requests session with retry logic."""
    s = requests.Session()
    retry = Retry(
        total=5, connect=3, read=5,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD", "OPTIONS"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

session = build_session()

def get_soup(url: str) -> BeautifulSoup:
    """Download and parse an HTML page with BeautifulSoup."""
    r = session.get(url, headers=HEADERS, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def sanitize(name: str) -> str:
    """Sanitize filenames for safe filesystem storage."""
    return re.sub(r'[\\/*?:"<>|]+', "_", name)

def extract_catalog_id_from_url(url: str) -> str | None:
    """Extract catalog ID (digits) from a catalog URL."""
    m = re.search(r"/catalog/(\d+)", url)
    return m.group(1) if m else None

def extract_catalog_id_from_filename(fname: str) -> str | None:
    """Assume filenames are saved as '<catalog_id>_README.pdf'."""
    m = re.match(r"(\d+)_README\.pdf$", fname)
    return m.group(1) if m else None

def filename_from_url(url: str) -> str:
    """Generate a standardized filename from a URL."""
    path = urlparse(url).path
    base = Path(path).name or "README.pdf"
    if "." not in base:
        base = "README.pdf"
    if base.lower() == "readme.pdf":
        cid = extract_catalog_id_from_url(url)
        if cid:
            base = f"{cid}_README.pdf"
    return sanitize(base)

def list_existing_catalog_ids(out_dir: Path) -> set[str]:
    """Return a set of catalog IDs already downloaded in the output folder."""
    out: set[str] = set()
    if out_dir.exists():
        for p in out_dir.glob("*.pdf"):
            cid = extract_catalog_id_from_filename(p.name)
            if cid:
                out.add(cid)
    return out

def download_one(url: str, out_dir: Path) -> bool:
    """Download a single README PDF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = filename_from_url(url)
    dest = out_dir / fn
    if dest.exists() and dest.stat().st_size > 0:
        tqdm.write(f"[skip] Exists: {dest.name}")
        return True
    try:
        with session.get(url, headers=HEADERS, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f, tqdm(total=total if total > 0 else None,
                                            unit="B", unit_scale=True, desc=fn) as pbar:
                for chunk in r.iter_content(chunk_size=1024*64):
                    if chunk:
                        f.write(chunk)
                        if total > 0:
                            pbar.update(len(chunk))
            tmp.rename(dest)
        return True
    except Exception as e:
        tqdm.write(f"[x] Failed {url} -> {e}")
        return False

# ======== Catalog scanning (/catalog/?page=N) ========
def discover_catalog_items(max_pages: int = 50):
    """Scrape catalog entry URLs from paginated catalog listings."""
    items, seen = [], set()
    for page in range(1, max_pages + 1):
        url = f"{CATALOG_ROOT}?page={page}"
        try:
            soup = get_soup(url)
        except Exception:
            break
        found_this = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if RE_ITEM.search(href):
                full = urljoin(BASE, href)
                if full not in seen:
                    seen.add(full)
                    items.append(full)
                    found_this += 1
        if found_this == 0:  # no more pages
            break
        time.sleep(0.25)
    return items

def _collect_downloads_from_page(url: str) -> list[str]:
    """Collect all direct README download links from a given page."""
    links = []
    try:
        soup = get_soup(url)
    except Exception:
        return links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(" ") or "").lower()
        if "/download/" in href and (RE_DOWNLOAD.search(href) or "readme" in text or "read me" in text):
            links.append(urljoin(BASE, href))
    # Deduplicate
    uniq, seen = [], set()
    for u in links:
        if "/download/" in u and u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq

def find_readme_links_on_item(item_url: str) -> list[str]:
    """
    Find README download links for a specific catalog entry.
    Searches both:
      1) The main catalog entry page
      2) The 'related-materials' tab
    """
    links = _collect_downloads_from_page(item_url)
    rel = item_url.rstrip("/") + "/related-materials"
    links += _collect_downloads_from_page(rel)
    # Deduplicate again
    out, seen = [], set()
    for u in links:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out

def auto_discover_batch(max_pages: int) -> list[str]:
    """Scrape up to max_pages of the catalog and return README download URLs."""
    pool = []
    for item in tqdm(discover_catalog_items(max_pages=max_pages), desc=f"Scanning catalog (≤ {max_pages} pages)", unit="page"):
        pool += find_readme_links_on_item(item)
        time.sleep(0.1)
    pool = list(dict.fromkeys(pool))
    return pool

def build_download_plan(limit: int, existing_ids: set[str]) -> list[str]:
    """
    Build a prioritized download plan:
      - Start from known direct links.
      - Skip already-downloaded catalog IDs.
      - If insufficient, expand scraping range until the limit is reached.
    """
    plan: list[str] = []
    seen_ids: set[str] = set()

    # Start with seed links (deduplicated and filtered)
    for u in list(dict.fromkeys(SEED_README_URLS)):
        cid = extract_catalog_id_from_url(u)
        if not cid:
            continue
        if cid in existing_ids or cid in seen_ids:
            continue
        plan.append(u)
        seen_ids.add(cid)
        if len(plan) >= limit:
            return plan

    # Expand scraping progressively
    max_pages = 30
    hard_cap_pages = 300
    while len(plan) < limit and max_pages <= hard_cap_pages:
        pool = auto_discover_batch(max_pages=max_pages)
        for u in pool:
            cid = extract_catalog_id_from_url(u)
            if not cid:
                continue
            if cid in existing_ids or cid in seen_ids:
                continue
            plan.append(u)
            seen_ids.add(cid)
            if len(plan) >= limit:
                break
        if len(plan) >= limit:
            break
        max_pages += 30
        tqdm.write(f"[i] Expanding search to {max_pages} pages...")

    return plan[:limit]

def main():
    print(f"[i] Target: {LIMIT} README files; output directory: {OUT_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Collect existing catalog IDs
    existing_ids = list_existing_catalog_ids(OUT_DIR)
    print(f"[i] Found {len(existing_ids)} existing catalog IDs: {sorted(existing_ids)[:8]}{' ...' if len(existing_ids)>8 else ''}")

    # Build download plan
    plan = build_download_plan(limit=LIMIT, existing_ids=existing_ids)
    if not plan:
        print("[!] Check network or adjust LIMIT/page cap.")
        sys.exit(2)

    print(f"[i] This run will attempt to download {len(plan)} files:")
    for u in plan[:5]:
        print("   -", u)
    if len(plan) > 5:
        print("   - ...")

    # Execute downloads
    ok = 0
    for u in plan:
        if download_one(u, OUT_DIR):
            ok += 1
        time.sleep(SLEEP_BETWEEN)

    final_existing = list_existing_catalog_ids(OUT_DIR)
    added = len(final_existing - existing_ids)
    print(f"[✓] Done: success (including skipped) {ok} / {len(plan)}; files saved in {OUT_DIR.resolve()}")

if __name__ == "__main__":
    main()

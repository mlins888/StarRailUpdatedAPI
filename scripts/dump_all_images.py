#!/usr/bin/env python3
"""
One-Time Bulk Image Downloader for StarRailRes

Strategy:
  - Crawl all local JSON files in db/en/
  - Recursively find any string value ending in ".png"
  - Filter for paths that belong in the "image/" or "icon/" folders
  - Download them concurrently from upstream if they don't exist locally
"""

import json
import urllib.request
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_BASE = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/"

def extract_image_paths(data, paths_set):
    """Recursively searches a JSON object for strings ending in .png"""
    if isinstance(data, dict):
        for val in data.values():
            if isinstance(val, str) and val.endswith(".png"):
                paths_set.add(val)
            else:
                extract_image_paths(val, paths_set)
    elif isinstance(data, list):
        for item in data:
            extract_image_paths(item, paths_set)

def download_image(rel_path):
    """Downloads a single image if it doesn't already exist."""
    local_path = REPO_ROOT / rel_path
    
    if local_path.exists():
        return False # Skip, already have it

    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{UPSTREAM_BASE}{rel_path}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "starrail-bulk-dumper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            with open(local_path, "wb") as f:
                f.write(r.read())
        return True # Success
    except Exception as e:
        print(f" [!] Failed to download {rel_path}: {e}", file=sys.stderr)
        return False

def main():
    print("🕷️ Crawling JSON files for image paths...")
    db_en = REPO_ROOT / "db" / "en"
    all_paths = set()
    
    # 1. Read all JSON files and extract paths
    for json_file in db_en.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                extract_image_paths(json.load(f), all_paths)
        except Exception as e:
            print(f"Error reading {json_file.name}: {e}")

    # 2. Filter for actual image/icon folders just to be safe
    valid_paths = [p for p in all_paths if p.startswith("image/") or p.startswith("icon/")]
    
    print(f"📊 Found {len(valid_paths)} total image references in the database.")
    print("🚀 Starting concurrent download (skipping existing images)...")
    
    downloaded_count = 0
    
    # 3. Fire up the multithreading engine!
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(download_image, valid_paths)
        for was_downloaded in results:
            if was_downloaded:
                downloaded_count += 1
                if downloaded_count % 50 == 0:
                    print(f"   ...downloaded {downloaded_count} new images so far...")

    print(f"✅ Done! Successfully grabbed {downloaded_count} missing images.")

if __name__ == "__main__":
    main()

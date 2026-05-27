#!/usr/bin/env python3
"""
Improved Bulk Image Downloader for StarRailRes

Strategy:
  - Instead of relying on JSON references, directly enumerate all available images
    from the upstream StarRailRes repository
  - Use the GitHub API to list all .png files in known image folders
  - Also crawl JSON files as a fallback to catch any edge cases
  - Download all discovered images concurrently
"""

import json
import urllib.request
import urllib.error
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Set

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_BASE = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/"
UPSTREAM_API_BASE = "https://api.github.com/repos/Mar-7th/StarRailRes/contents/"

# Key image folders to search
IMAGE_FOLDERS = [
    "image/character_preview",
    "image/character_portrait",
    "image/item",
    "image/light_cone",
    "image/light_cone_preview",
    "image/light_cone_portrait",
    "image/relic",
    "image/relic_icon",
    "image/equipment",
    "image/skill",
    "icon",
]

def extract_image_paths_from_json(data, paths_set):
    """Recursively searches a JSON object for strings ending in .png"""
    if isinstance(data, dict):
        for val in data.values():
            if isinstance(val, str) and val.endswith(".png"):
                paths_set.add(val)
            else:
                extract_image_paths_from_json(val, paths_set)
    elif isinstance(data, list):
        for item in data:
            extract_image_paths_from_json(item, paths_set)

def list_images_in_folder(folder_path: str) -> Set[str]:
    """
    Use GitHub API to list all .png files in a folder from the upstream repo.
    Returns a set of relative paths.
    """
    images = set()
    url = f"{UPSTREAM_API_BASE}{folder_path}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "starrail-bulk-dumper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            items = json.loads(r.read().decode('utf-8'))
            
            # Handle GitHub API response
            if isinstance(items, list):
                for item in items:
                    if item.get("type") == "file" and item["name"].endswith(".png"):
                        images.add(item["path"])
                    elif item.get("type") == "dir":
                        # Recursively search subdirectories
                        images.update(list_images_in_folder(item["path"]))
            
            print(f"✓ Found {len(images)} images in {folder_path}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"⚠ Warning: Failed to fetch {folder_path}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"⚠ Warning: Error fetching {folder_path}: {e}", file=sys.stderr)
    
    return images

def download_image(rel_path: str) -> bool:
    """Downloads a single image if it doesn't already exist."""
    local_path = REPO_ROOT / rel_path
    
    if local_path.exists():
        return False  # Skip, already have it

    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{UPSTREAM_BASE}{rel_path}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "starrail-bulk-dumper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            with open(local_path, "wb") as f:
                f.write(r.read())
        return True  # Success
    except Exception as e:
        print(f"✗ Failed to download {rel_path}: {e}", file=sys.stderr)
        return False

def main():
    print("🕷️  Starting improved image downloader...\n")
    
    all_paths = set()
    
    # Strategy 1: Use GitHub API to enumerate all images in known folders
    print("📡 Fetching image list from upstream using GitHub API...")
    for folder in IMAGE_FOLDERS:
        folder_images = list_images_in_folder(folder)
        all_paths.update(folder_images)
    
    print(f"\n✓ Found {len(all_paths)} images via API enumeration\n")
    
    # Strategy 2: Also crawl local JSON files as a fallback
    print("🕷️  Crawling local JSON files for additional image references...")
    db_en = REPO_ROOT / "db" / "en"
    json_paths = set()
    
    if db_en.exists():
        for json_file in db_en.glob("**/*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    extract_image_paths_from_json(json.load(f), json_paths)
            except Exception as e:
                print(f"⚠ Error reading {json_file.name}: {e}", file=sys.stderr)
        
        # Filter for image/icon folders
        json_paths = {p for p in json_paths if p.startswith("image/") or p.startswith("icon/")}
        print(f"✓ Found {len(json_paths)} image references in JSON\n")
        
        # Merge with API results
        all_paths.update(json_paths)
    
    print(f"📊 Total unique images to download: {len(all_paths)}\n")
    
    if not all_paths:
        print("❌ No images found! Check upstream connectivity.")
        return
    
    print("🚀 Starting concurrent download (skipping existing images)...")
    downloaded_count = 0
    failed_count = 0
    skipped_count = 0
    
    # Download with multithreading
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(download_image, sorted(all_paths))
        for result in results:
            if result is True:
                downloaded_count += 1
            elif result is False:
                skipped_count += 1
            else:
                failed_count += 1
            
            total_processed = downloaded_count + skipped_count + failed_count
            if total_processed % 50 == 0:
                print(f"   ...processed {total_processed}/{len(all_paths)} ({downloaded_count} new)")

    print(f"\n✅ Download complete!")
    print(f"   📥 Downloaded: {downloaded_count}")
    print(f"   ⏭️  Skipped (already exist): {skipped_count}")
    print(f"   ❌ Failed: {failed_count}")

if __name__ == "__main__":
    main()

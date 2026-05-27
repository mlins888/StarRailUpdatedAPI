#!/usr/bin/env python3
"""
Bulk Image Downloader for StarRailRes
=====================================

Crawls every JSON file under db/ to collect image paths, then downloads any
images that aren't already present locally.

Compared to earlier versions:
  - Uses recursive glob so all language folders are crawled, not just db/en/
  - Retries with exponential backoff on transient failures
  - Verifies downloaded bytes are a real PNG before writing
  - Per-file logging only on failure; aggregate summary at the end
  - Idempotent: re-running only fetches what's missing

Run from the repo root:
    python scripts/download_images.py            # default
    python scripts/download_images.py --workers 4    # be gentler on upstream
    python scripts/download_images.py --force        # re-download even if present
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_ROOT = REPO_ROOT / "db"

# Same fallback pattern as the sync script: jsDelivr first, raw GitHub as backup.
UPSTREAM_BASES = [
    "https://cdn.jsdelivr.net/gh/Mar-7th/StarRailRes@master/",
    "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/",
]

# PNG magic bytes — verify we got an image, not an HTML 404 page or a rate-limit notice.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Folders we consider "real" asset paths. Anything outside these is ignored
# even if it happens to end in .png (defensive against odd JSON values).
VALID_PREFIXES = ("image/", "icon/")


def collect_image_paths() -> set[str]:
    """Recursively walk every db/**/*.json and extract every string ending in .png."""
    paths: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            if node.endswith(".png") and node.startswith(VALID_PREFIXES):
                paths.add(node)

    json_files = list(DB_ROOT.glob("**/*.json"))
    print(f"Scanning {len(json_files)} JSON file(s) under {DB_ROOT.relative_to(REPO_ROOT)}/ ...")
    for jf in json_files:
        try:
            with jf.open("r", encoding="utf-8") as f:
                walk(json.load(f))
        except json.JSONDecodeError as e:
            print(f"  ! Skipping malformed JSON {jf.relative_to(REPO_ROOT)}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  ! Error reading {jf.relative_to(REPO_ROOT)}: {e}", file=sys.stderr)

    return paths


def fetch_bytes(url: str, timeout: int = 20) -> bytes | None:
    """GET url, return bytes or None on real 404. Raises on other HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "starrail-image-sync"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_one(rel_path: str, force: bool) -> tuple[str, str]:
    """
    Download a single image. Returns (rel_path, status) where status is one of:
      'downloaded', 'skipped' (already present), 'missing' (404 on all mirrors),
      or 'failed: <reason>'.
    """
    local_path = REPO_ROOT / rel_path
    if local_path.exists() and not force:
        return rel_path, "skipped"

    last_err: str | None = None
    # Try each mirror, with 3 attempts per mirror, exponential backoff.
    for base in UPSTREAM_BASES:
        url = base + rel_path
        for attempt in range(3):
            try:
                data = fetch_bytes(url)
                if data is None:
                    # Real 404: file doesn't exist at this mirror. Try next mirror.
                    last_err = f"404 at {base}"
                    break
                if not data.startswith(PNG_MAGIC):
                    # We got bytes, but it's not a PNG — likely an HTML error page.
                    last_err = f"non-PNG response at {base} ({len(data)} bytes)"
                    break
                # Looks good. Write atomically via temp file.
                local_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = local_path.with_suffix(local_path.suffix + ".tmp")
                tmp.write_bytes(data)
                tmp.replace(local_path)
                return rel_path, "downloaded"
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code} at {base}"
                # 429 / 5xx: back off and retry the same mirror.
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 ** attempt)
                    continue
                # Other client errors: stop retrying this mirror.
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.5 ** attempt)
                continue

    # If we got here, no mirror succeeded.
    if last_err and last_err.startswith("404"):
        return rel_path, "missing"
    return rel_path, f"failed: {last_err}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent downloads (default: 8; lower if you get throttled)")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the file already exists locally")
    args = ap.parse_args()

    paths = collect_image_paths()
    if not paths:
        print("No image paths found in JSON. Nothing to do.")
        return 0
    print(f"Found {len(paths)} unique image path(s) referenced in JSON.\n")

    counts = {"downloaded": 0, "skipped": 0, "missing": 0, "failed": 0}
    failures: list[tuple[str, str]] = []
    missing: list[str] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(download_one, p, args.force) for p in sorted(paths)]
        for i, fut in enumerate(as_completed(futs), 1):
            rel, status = fut.result()
            if status == "downloaded":
                counts["downloaded"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            elif status == "missing":
                counts["missing"] += 1
                missing.append(rel)
            else:
                counts["failed"] += 1
                failures.append((rel, status))
            if i % 100 == 0 or i == len(futs):
                done = counts["downloaded"] + counts["skipped"]
                print(f"  progress: {i}/{len(futs)}  "
                      f"(new: {counts['downloaded']}, "
                      f"skipped: {counts['skipped']}, "
                      f"missing: {counts['missing']}, "
                      f"failed: {counts['failed']})")

    print("\n--- summary ---")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    if missing:
        print(f"\n{len(missing)} path(s) not found upstream (probably referenced "
              f"in JSON but never uploaded to Mar-7th). First 10:")
        for p in missing[:10]:
            print(f"  - {p}")

    if failures:
        print(f"\n{len(failures)} path(s) failed for non-404 reasons:")
        for p, reason in failures[:20]:
            print(f"  - {p}  ({reason})")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

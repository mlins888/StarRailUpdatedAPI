#!/usr/bin/env python3
"""
Sync StarRailRes upstream data into this fork's db/ folder.

Strategy (merge mode):
  - For each JSON file, fetch upstream and load local (if present).
  - For dict-shaped files keyed by ID (characters, skills, ranks, etc.):
      result = local | upstream      (upstream wins on shared keys)
    This preserves any custom IDs that don't exist upstream
    (e.g. Saber=1014, Archer=1015, Silver Wolf LV.999=1344).
  - For list-shaped files: just take upstream as-is.
  - Handle the affix rename: upstream is 'relic_main_affixs' (no 'e'),
    local is 'relic_main_affixes'. Same for sub.
  - Files that only exist in VizualAbstract's mirror (achievements, avatars,
    descriptions, nickname, simulated_*) are left untouched.

Run locally:    python scripts/sync_upstream.py
Run in CI:      same command; the workflow handles git commit/push.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# jsDelivr is faster than raw.githubusercontent.com and friendlier to CI rate limits.
UPSTREAM_BASE = "https://cdn.jsdelivr.net/gh/Mar-7th/StarRailRes@master/index_new"

LANGUAGES = ["cn", "cht", "de", "en", "es", "fr", "jp", "kr", "pt", "ru", "th", "vi"]

# (upstream_filename, local_filename) pairs.
# Most files are 1:1, two have affix renames.
FILE_MAPPING = [
    ("paths.json",                   "paths.json"),
    ("elements.json",                "elements.json"),
    ("properties.json",              "properties.json"),
    ("characters.json",              "characters.json"),
    ("character_ranks.json",         "character_ranks.json"),
    ("character_skills.json",        "character_skills.json"),
    ("character_skill_trees.json",   "character_skill_trees.json"),
    ("character_promotions.json",    "character_promotions.json"),
    ("light_cones.json",             "light_cones.json"),
    ("light_cone_ranks.json",        "light_cone_ranks.json"),
    ("light_cone_promotions.json",   "light_cone_promotions.json"),
    ("relics.json",                  "relics.json"),
    ("relic_sets.json",              "relic_sets.json"),
    ("relic_main_affixs.json",       "relic_main_affixes.json"),  # rename
    ("relic_sub_affixs.json",        "relic_sub_affixes.json"),   # rename
    ("items.json",                   "items.json"),
]


def fetch(url: str) -> dict | list | None:
    """GET a URL and parse JSON. Returns None on 404 (upstream may not have every lang/file)."""
    req = urllib.request.Request(url, headers={"User-Agent": "starrail-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def load_local(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def merge(local, upstream):
    """
    Merge upstream into local. Upstream wins on overlapping keys.
    - dict + dict: {**local, **upstream}  -> custom-only keys (Saber 1014 etc.) survive
    - list (or anything else): upstream replaces local entirely
    - if either side is None, return the other
    """
    if upstream is None:
        return local
    if local is None:
        return upstream
    if isinstance(local, dict) and isinstance(upstream, dict):
        return {**local, **upstream}
    # Lists or scalars: trust upstream
    return upstream


def write_local(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(", ", ": "))
        f.write("\n")


def sync_one(lang: str, upstream_name: str, local_name: str, summary: dict) -> None:
    url = f"{UPSTREAM_BASE}/{lang}/{upstream_name}"
    local_path = REPO_ROOT / "db" / lang / local_name

    upstream = fetch(url)
    if upstream is None:
        summary.setdefault("missing_upstream", []).append(f"{lang}/{upstream_name}")
        return

    local = load_local(local_path)
    merged = merge(local, upstream)

    # Track what happened (for the CI log / commit message).
    if isinstance(local, dict) and isinstance(upstream, dict):
        local_keys = set(local or {})
        up_keys = set(upstream)
        custom_preserved = sorted(local_keys - up_keys)
        added = sorted(up_keys - local_keys)
        if custom_preserved or added:
            summary.setdefault("files", []).append({
                "file": f"{lang}/{local_name}",
                "custom_preserved": custom_preserved,
                "added_from_upstream": added,
            })

    write_local(local_path, merged)


def main() -> int:
    # Default to English only; pass --all-langs to do every language.
    langs = LANGUAGES if "--all-langs" in sys.argv else ["en"]
    summary: dict = {}

    for lang in langs:
        for upstream_name, local_name in FILE_MAPPING:
            sync_one(lang, upstream_name, local_name, summary)

    # Write the run summary so CI can use it in the commit body.
    out = REPO_ROOT / "scripts" / ".last_sync_summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
apply_scores.py — Persist rubric-based relevance scores (assigned by a human
or agent reading each abstract) into the paper store.

Reads a JSON file mapping paper id -> {relevance_score, relevance_rationale}
and merges it into the store record-by-record (these two fields are always
overwritten — a re-score is an explicit correction, unlike `paper_store.upsert`
which only ever fills gaps). Re-exports the question's papers.md afterward,
sorted with `relevance_score` taking priority over the cheap prescreen signals.

Scores file format:
    {
      "doi:10.1234/example": {"relevance_score": 4, "relevance_rationale": "..."},
      "title:someoldpaperwithnodoi": {"relevance_score": 1, "relevance_rationale": "..."}
    }

`relevance_score` is on whatever scale you choose (0-5 is a reasonable
default) — this tool doesn't enforce a range, it just stores what it's given.

Usage:
    python tool/apply_scores.py scores.json --question "..." [--slug ...]
"""

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, load, save, export_question


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scores_file", help="JSON file: {paper_id: {relevance_score, relevance_rationale}}")
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    scores = json.loads(Path(args.scores_file).read_text(encoding="utf-8"))

    updated = 0
    missing = []
    for pid, fields in scores.items():
        if pid not in store:
            missing.append(pid)
            continue
        store[pid]["relevance_score"] = fields.get("relevance_score")
        store[pid]["relevance_rationale"] = fields.get("relevance_rationale", "")
        updated += 1

    save(store, store_path)
    qdir = export_question(store, args.question, args.slug)

    print(f"Applied relevance scores to {updated}/{len(scores)} papers.")
    print(f"Refreshed {qdir / 'papers.md'}")
    if missing:
        sys.stderr.write(
            f"[WARN] {len(missing)} id(s) in {args.scores_file} not found in the store "
            f"(wrong id, or typo): {missing[:5]}{'...' if len(missing) > 5 else ''}\n"
        )


if __name__ == "__main__":
    main()

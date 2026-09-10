#!/usr/bin/env python3
"""
triage_papers.py — Bucket scored papers into core/borderline/excluded tiers.

Reads `relevance_score` (from apply_scores.py) and writes a `status` field:
    core        relevance_score >= --core-min   (default 4)
    borderline  --exclude-max < relevance_score < --core-min
    excluded    relevance_score <= --exclude-max (default 1)
    unscored    no relevance_score yet — left alone, not swept into any tier

Nothing is deleted — `excluded` papers stay in the store with full
provenance, just tagged so downstream tools (citation expansion, exports)
can filter them out by default. Re-running after new scores just recomputes
`status` for every record with a score; already-`core` papers don't need
re-triaging unless their score changed.

Usage:
    python tool/triage_papers.py --question "..." [--slug ...] [--core-min 4] [--exclude-max 1]
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, BRAINSTORM_ROOT, load, save, export_question, export_markdown, question_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--core-min", type=float, default=4, help="relevance_score >= this -> core (default: 4)")
    ap.add_argument("--exclude-max", type=float, default=1, help="relevance_score <= this -> excluded (default: 1)")
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    for r in records:
        score = r.get("relevance_score")
        if score is None:
            r["status"] = "unscored"
        elif score >= args.core_min:
            r["status"] = "core"
        elif score <= args.exclude_max:
            r["status"] = "excluded"
        else:
            r["status"] = "borderline"

    save(store, store_path)
    export_question(store, args.question, args.slug)

    qdir = question_dir(args.question, args.slug, BRAINSTORM_ROOT)
    core_path = qdir / "core_papers.md"
    export_markdown(
        store, core_path,
        filter_fn=lambda r: r.get("question") == args.question and r.get("status") == "core",
    )

    counts = Counter(r["status"] for r in records)
    print(f"Triaged {len(records)} papers:")
    for status in ("core", "borderline", "excluded", "unscored"):
        print(f"  {status}: {counts.get(status, 0)}")
    print(f"Core-tier view -> {core_path}")


if __name__ == "__main__":
    main()

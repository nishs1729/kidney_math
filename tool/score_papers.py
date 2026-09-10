#!/usr/bin/env python3
"""
score_papers.py — Compute cheap, zero-token relevance signals for every paper
stored under a question, and print a ranked table to guide a rubric-based
review pass.

Writes two fields onto each matching store record:
    heuristic_score    0-100, from citation velocity + tag coverage + review boost
    similarity_score   0-1, TF-IDF cosine similarity to the question text

Does NOT assign `relevance_score` — that's a judgment call on the actual
abstract text, meant to be made by whoever (human or agent) is running the
loop, then persisted with `apply_scores.py`. This script only produces the
cheap pre-ranking so that review pass can start with the most-likely-relevant
papers instead of reading the store in arbitrary order.

Usage:
    python tool/score_papers.py --question "..." [--slug ...] [--top N]
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, load, save, export_question
from tool.scoring import heuristic_score, similarity_scores


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--top", type=int, default=25, help="How many ranked rows to print (default: 25)")
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    max_tags = max((len(r.get("query_tags", [])) for r in records), default=1)
    sim_scores = similarity_scores(args.question, records)

    for r in records:
        r["similarity_score"] = sim_scores.get(r["id"], 0.0)
        r["heuristic_score"] = heuristic_score(r, max_tags=max_tags)

    save(store, store_path)
    export_question(store, args.question, args.slug)

    def combo(r):
        return r["heuristic_score"] + r["similarity_score"] * 100

    ranked = sorted(records, key=combo, reverse=True)

    print(f"Scored {len(records)} papers -> {store_path}")
    print(f"{'combo':>6} {'heur':>6} {'sim':>5}  title")
    for r in ranked[: args.top]:
        print(f"{combo(r):6.1f} {r['heuristic_score']:6.1f} {r['similarity_score']:5.2f}  {(r.get('title') or '')[:90]}")
    if len(ranked) > args.top:
        print(f"... ({len(ranked) - args.top} more; see papers.md for the full ranked list)")


if __name__ == "__main__":
    main()

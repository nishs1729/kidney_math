#!/usr/bin/env python3
"""
round_report.py — Per-round yield breakdown and a saturation verdict.

`process.md` Step 11: stop looping once new rounds stop contributing
meaningful new papers. Reports, for each `first_seen_round` present in the
store for this question, how many papers it introduced in total and how
many turned out `status: core` (from triage_papers.py) — the number that
actually matters for deciding whether another round of taxonomy refinement
or citation expansion is worth running.

Pass --by-tag for a second table, per `query_tags` entry, of how many papers
that taxonomy cell (or "citation:backward"/"citation:forward") matched and
how many of those turned out core — use it to see which taxonomy cells are
worth keeping/narrowing/dropping when writing the next round's taxonomy.

A paper's `first_seen_round` is fixed at whichever round introduced it, so
this reads directly off the store — no separate round-history bookkeeping
needed.

Usage:
    python tool/round_report.py --question "..." [--slug ...]
        [--saturation-rounds 2] [--saturation-threshold 3]
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, load


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--core-min", type=float, default=4,
                     help="Fallback core threshold on relevance_score, only used for papers without a "
                          "status field yet (default: 4)")
    ap.add_argument("--saturation-rounds", type=int, default=2,
                     help="Consecutive low-yield rounds needed to call saturation (default: 2)")
    ap.add_argument("--saturation-threshold", type=int, default=3,
                     help="A round contributing fewer than this many new core papers counts as low-yield (default: 3)")
    ap.add_argument("--by-tag", action="store_true", help="Also print a per-query_tag productivity table")
    args = ap.parse_args()

    store = load(Path(args.store_path))
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    def is_core(r):
        if r.get("status"):
            return r["status"] == "core"
        return (r.get("relevance_score") or -1) >= args.core_min

    by_round = defaultdict(lambda: {"total": 0, "core": 0})
    for r in records:
        rnd = r.get("first_seen_round", 0)
        by_round[rnd]["total"] += 1
        if is_core(r):
            by_round[rnd]["core"] += 1

    rounds = sorted(by_round)
    print(f"{'round':>6} {'new_total':>10} {'new_core':>9}")
    for rnd in rounds:
        d = by_round[rnd]
        print(f"{rnd:>6} {d['total']:>10} {d['core']:>9}")

    if args.by_tag:
        by_tag = defaultdict(lambda: {"total": 0, "core": 0})
        for r in records:
            for tag in r.get("query_tags", []):
                by_tag[tag]["total"] += 1
                if is_core(r):
                    by_tag[tag]["core"] += 1
        tags = sorted(by_tag, key=lambda t: (-by_tag[t]["core"], -by_tag[t]["total"]))
        print(f"\n{'query_tag':<35} {'total':>6} {'core':>5}")
        for tag in tags:
            d = by_tag[tag]
            print(f"{tag:<35} {d['total']:>6} {d['core']:>5}")

    tail = [by_round[r]["core"] for r in rounds[-args.saturation_rounds:]]
    if len(rounds) >= args.saturation_rounds and all(c < args.saturation_threshold for c in tail):
        print(
            f"\nSATURATED: the last {args.saturation_rounds} round(s) each contributed fewer than "
            f"{args.saturation_threshold} new core papers ({tail}). Consider stopping the loop."
        )
    else:
        print(
            f"\nNot yet saturated (last {args.saturation_rounds} round(s): {tail}, "
            f"threshold {args.saturation_threshold}/round). Continue if there are still "
            "unexplored taxonomy cells or gaps to chase."
        )


if __name__ == "__main__":
    main()

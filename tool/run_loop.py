#!/usr/bin/env python3
"""
run_loop.py — Steps 1-4 of the search loop, end-to-end, for any question.

Replaces the throwaway per-round scripts written directly against
search_utils.py: this is a single reusable CLI entry point that takes a
concepts file (Step 1 input) and a taxonomy spec (Step 2 input), runs the
batch (Step 3), dedupes (Step 4), and writes into the shared paper store
(tool/paper_store.py) instead of a new timestamped file — so a second run on
the same question only reports genuinely new papers, and a run on a
different question can't collide with it.

By default this prints only a summary (source/tag counts, dedup delta, new
vs. already-seen); pass --verbose to also print titles, or --export-md to
render the full store as Markdown.

Usage:
    python tool/run_loop.py \\
        --question "What mathematical models have been used to study X?" \\
        --concepts-file concepts.txt \\
        --taxonomy-file taxonomy_round1.py \\
        --round 1

concepts.txt: one free-text concept per line (Step 1 — MeSH expansion is
    printed to stderr as a table to inform how you write the taxonomy file;
    it is not auto-applied).

taxonomy_round1.py: a plain Python module defining ROWS, COLUMNS, and
    (optionally) EXTRA_AND as top-level literals — a .py spec file instead of
    JSON so query phrases can use plain double quotes without escaping:

    ROWS = [
        {"tag": "TGF", "or_terms": ['"tubuloglomerular feedback"', '"macula densa signal"']},
    ]
    COLUMNS = {
        "model": '("mathematical model" OR "computational model")',
    }
    EXTRA_AND = "kidney"

    Kept as a saved, git-diffable file (not just an inline Python call) so
    each round's exact query provenance survives past the script that
    produced it — the same durability reasoning behind paper_store.py's JSONL
    format.
"""

import argparse
import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.search_utils import expand_concepts, run_batch
from tool.query_utils import build_queries
from tool.paper_store import DEFAULT_STORE_PATH, load, upsert, export_markdown


def load_taxonomy(path: Path) -> Tuple[list, dict, Optional[str]]:
    """Import ROWS/COLUMNS/EXTRA_AND from a taxonomy .py spec file."""
    spec = importlib.util.spec_from_file_location(f"taxonomy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.ROWS
    columns = module.COLUMNS
    extra_and = getattr(module, "EXTRA_AND", None)
    return rows, columns, extra_and


def _print_vocab_table(vocab: dict) -> None:
    sys.stderr.write("\n[Step 1] MeSH vocabulary (use this to write the taxonomy file's or_terms):\n")
    for concept, entry in vocab.items():
        if entry["status"] == "found":
            sys.stderr.write(f"  {concept!r} -> {entry['mesh_name']!r} "
                              f"(entry terms: {entry['entry_terms'][:5]})\n")
        else:
            sys.stderr.write(f"  {concept!r} -> not found in MeSH; use free text + known synonyms\n")
    sys.stderr.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True, help="The scientific question this round is for")
    ap.add_argument("--concepts-file", help="One free-text concept per line (Step 1)")
    ap.add_argument("--taxonomy-file", required=True, help="Python taxonomy spec, e.g. taxonomy_round1.py (Step 2)")
    ap.add_argument("--round", type=int, required=True, help="Round number for provenance")
    ap.add_argument("--sources", nargs="+", default=["pubmed", "openalex", "biorxiv", "medrxiv"])
    ap.add_argument("--max-per-query", type=int, default=15)
    ap.add_argument("--year-range", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--export-md", default=None, help="Also render the full store to this Markdown path")
    ap.add_argument("--verbose", action="store_true", help="Print every new paper's title, not just counts")
    args = ap.parse_args()

    if args.concepts_file:
        concepts = [
            line.strip() for line in Path(args.concepts_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        vocab = expand_concepts(concepts)
        _print_vocab_table(vocab)

    rows, columns, extra_and = load_taxonomy(Path(args.taxonomy_file))
    query_tags = build_queries(rows=rows, columns=columns, extra_and=extra_and)
    sys.stderr.write(f"[Step 2] Built {len(query_tags)} queries from {args.taxonomy_file} (linted clean).\n")

    articles = run_batch(
        queries=list(query_tags.keys()),
        query_tags=query_tags,
        sources=args.sources,
        max_per_query=args.max_per_query,
        compact=True,
        year_range=args.year_range,
    )

    store_path = Path(args.store_path)
    store = load(store_path)
    new_ids = upsert(store, articles, question=args.question, round_num=args.round, path=store_path)

    # --- Summary (default output; no full dumps) ---
    by_source = Counter(a["source"] for a in articles)
    by_tag = Counter(a.get("query_tag", "") for a in articles)
    print(f"\nRound {args.round}: {len(articles)} results this round, "
          f"{len(new_ids)} new to the store, {len(articles) - len(new_ids)} already seen.")
    print(f"By source: {dict(by_source)}")
    print(f"By tag:    {dict(by_tag)}")
    print(f"Store now holds {len(store)} papers total -> {store_path}")

    if args.verbose:
        print("\nNew papers this round:")
        for pid in new_ids:
            rec = store[pid]
            print(f"  - {rec['title']} ({rec.get('source')}, {rec.get('year')})")

    if args.export_md:
        export_markdown(store, Path(args.export_md))
        print(f"Markdown export -> {args.export_md}")


if __name__ == "__main__":
    main()

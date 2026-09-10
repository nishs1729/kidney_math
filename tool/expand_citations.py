#!/usr/bin/env python3
"""
expand_citations.py — Pull backward references and forward citations for
core-tier papers, and add anything new to the store as round-N candidates.

`process.md` Step 8: the single highest-yield way to find relevant papers
that keyword search misses entirely (older terminology, different
vocabulary, a subfield's own jargon) — instead of only ever finding papers
that happen to match a taxonomy query string, this follows the citation
graph outward from papers already confirmed relevant (`status: core`, from
triage_papers.py).

For each core paper:
  - backward: up to --max-per-paper of its `referenced_works` (what it cites)
  - forward:  up to --max-per-paper works citing it (`cited_by_api_url`)

Papers sourced from PubMed/bioRxiv/medRxiv don't carry these fields directly
(only OpenAlex captures them) — this script backfills them via an OpenAlex
DOI lookup first, so citation expansion isn't limited to papers that
happened to come from OpenAlex originally.

New candidates are added with `query_tag` = "citation:backward" or
"citation:forward" (so they're visible in `query_tags` like any taxonomy
cell) at the given `--round`, deduplicated against the existing store the
same way a normal search round is.

Usage:
    python tool/expand_citations.py --question "..." --slug "..." --round 2
        [--min-relevance 4] [--max-per-paper 25]
"""

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.search_utils import fetch_openalex_by_dois, fetch_openalex_by_ids, fetch_citing_works, deduplicate
from tool.paper_store import DEFAULT_STORE_PATH, load, save, upsert, export_question


def _backfill_citation_fields(records, mailto=None):
    """For core papers missing referenced_works/cited_by_api_url, look them up on OpenAlex by DOI."""
    need = [r for r in records if not r.get("referenced_works") and not r.get("cited_by_api_url") and r.get("doi")]
    if not need:
        return
    dois = [r["doi"] for r in need]
    full = fetch_openalex_by_dois(dois, compact=True, mailto=mailto)
    by_doi = {f["doi"].strip().lower(): f for f in full if f.get("doi")}
    for r in need:
        f = by_doi.get(r["doi"].strip().lower())
        if f:
            r["referenced_works"] = f.get("referenced_works", [])
            r["cited_by_api_url"] = f.get("cited_by_api_url", "")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--round", type=int, required=True, help="Round number for provenance")
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--min-relevance", type=float, default=None,
                     help="Expand from papers with relevance_score >= this (default: status == 'core', "
                          "from triage_papers.py; set this to bypass triage and use a raw score cutoff instead)")
    ap.add_argument("--max-per-paper", type=int, default=25,
                     help="Cap backward refs and forward citations fetched per core paper (default: 25)")
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    if args.min_relevance is not None:
        seeds = [r for r in records if (r.get("relevance_score") or -1) >= args.min_relevance]
    else:
        seeds = [r for r in records if r.get("status") == "core"]
    if not seeds:
        sys.stderr.write(
            "[ERROR] no seed papers to expand from. Run triage_papers.py first (sets status='core'), "
            "or pass --min-relevance to select by relevance_score directly.\n"
        )
        sys.exit(1)

    sys.stderr.write(f"[INFO] Expanding citations from {len(seeds)} seed paper(s).\n")
    _backfill_citation_fields(seeds)
    save(store, store_path)  # persist any newly-backfilled citation-graph fields onto existing records

    known_openalex_ids = {
        r["ids"]["openalex_id"] for r in store.values()
        if r.get("ids", {}).get("openalex_id")
    }

    backward_ids = set()
    for r in seeds:
        for wid in (r.get("referenced_works") or [])[: args.max_per_paper]:
            if wid not in known_openalex_ids:
                backward_ids.add(wid)

    backward_articles = []
    if backward_ids:
        sys.stderr.write(f"[INFO] Fetching {len(backward_ids)} backward reference(s).\n")
        backward_articles = fetch_openalex_by_ids(list(backward_ids), compact=True)
        for a in backward_articles:
            a["query_tag"] = "citation:backward"

    forward_articles = []
    for i, r in enumerate(seeds, 1):
        url = r.get("cited_by_api_url")
        if not url:
            continue
        sys.stderr.write(f"[INFO] Forward citations {i}/{len(seeds)}: {(r.get('title') or '')[:60]}\n")
        citing = fetch_citing_works(url, max_results=args.max_per_paper, compact=True)
        forward_articles.extend(citing)
        time.sleep(0.2)
    for a in forward_articles:
        a.setdefault("query_tag", "citation:forward")

    combined = deduplicate(backward_articles + forward_articles)

    new_ids = upsert(store, combined, question=args.question, round_num=args.round, slug=args.slug, path=store_path)
    export_question(store, args.question, args.slug)

    print(f"Backward references fetched: {len(backward_articles)}")
    print(f"Forward citations fetched:   {len(forward_articles)}")
    print(f"After dedup:                 {len(combined)}")
    print(f"New to the store:            {len(new_ids)}")
    print(f"Store now holds {len(store)} papers total -> {store_path}")


if __name__ == "__main__":
    main()

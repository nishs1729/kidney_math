#!/usr/bin/env python3
"""
enrich_abstracts.py — Fetch abstracts for paper-store records that don't have one.

Generalizes this session's one-off add_abstracts.py: `run_batch`/`run_loop.py`
fetch `compact=True` (no abstracts) for the broad sweep, so most records in
the store need a follow-up fetch. This walks the store, batches PubMed
records by PMID (one `efetch` call per 100), and looks up bioRxiv/medRxiv
records individually by DOI against Europe PMC, then writes only the
records that changed.

Usage:
    python tool/enrich_abstracts.py [--store-path brainstorm/papers.jsonl] [--question "..."]
"""

import argparse
import sys
import time
import urllib.parse
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json
from tool.search_utils import fetch_pubmed_by_pmids, _http_get, _EPMC_BASE
from tool.paper_store import DEFAULT_STORE_PATH, load, save


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--question", default=None, help="Only enrich records for this question (default: all)")
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)

    records = list(store.values())
    if args.question:
        records = [r for r in records if r.get("question") == args.question]

    missing = [r for r in records if not r.get("abstract")]
    sys.stderr.write(f"[INFO] {len(missing)}/{len(records)} records missing an abstract.\n")

    pubmed_missing = [r for r in missing if r["source"] == "pubmed" and r.get("ids", {}).get("pmid")]
    rxiv_missing = [r for r in missing if r["source"] in ("biorxiv", "medrxiv") and r.get("doi")]

    updated = 0

    pmids = [r["ids"]["pmid"] for r in pubmed_missing]
    abstract_by_pmid = {}
    for i in range(0, len(pmids), 100):
        chunk = pmids[i:i + 100]
        full = fetch_pubmed_by_pmids(chunk, compact=False)
        for f in full:
            abstract_by_pmid[f["ids"]["pmid"]] = f
        time.sleep(0.34)

    for r in pubmed_missing:
        full = abstract_by_pmid.get(r["ids"]["pmid"])
        if full and full.get("abstract"):
            r["abstract"] = full["abstract"]
            if full.get("pub_types") and not r.get("pub_types"):
                r["pub_types"] = full["pub_types"]
                r["is_review"] = full["is_review"]
            updated += 1

    for r in rxiv_missing:
        doi = r["doi"]
        q = f'DOI:"{doi}" AND SRC:PPR'
        qs = urllib.parse.urlencode({"query": q, "format": "json", "resultType": "core", "pageSize": 1})
        try:
            raw = _http_get(f"{_EPMC_BASE}/search?{qs}")
            data = json.loads(raw)
            results = (data.get("resultList") or {}).get("result", [])
            if results and results[0].get("abstractText"):
                r["abstract"] = results[0]["abstractText"]
                updated += 1
        except Exception as exc:
            sys.stderr.write(f"[WARN] abstract fetch failed for {doi}: {exc}\n")
        time.sleep(0.34)

    save(store, store_path)
    print(f"Updated {updated} records with abstracts. Store -> {store_path}")


if __name__ == "__main__":
    main()

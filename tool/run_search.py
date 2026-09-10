#!/usr/bin/env python3
"""
run_search.py — Literature Search Runner.

Executes a keyword search across PubMed, OpenAlex, and bioRxiv/medRxiv,
deduplicates results, and saves a clean Markdown report (+ optional JSON) to
the brainstorm/ directory.

Usage:
  python tool/run_search.py "SGLT2 kidney tubule transport"
  python tool/run_search.py "renal hemodynamics autoregulation" -n 15 --compact
  python tool/run_search.py "glomerular filtration model" --sources pubmed openalex
  python tool/run_search.py "CKD progression biomarkers" --year 2020-2024 --save-json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root on sys.path
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_dotenv(repo_root: str) -> None:
    """Load key=value pairs from .env into os.environ (stdlib only, no overwrite)."""
    env_path = Path(repo_root) / ".env"
    if not env_path.exists():
        return
    with env_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value


_load_dotenv(_REPO_ROOT)

from tool.search_utils import search_all, deduplicate, run_batch


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def _source_badge(source: str) -> str:
    badges = {
        "pubmed": "PubMed",
        "openalex": "OpenAlex",
        "biorxiv": "bioRxiv",
        "medrxiv": "medRxiv",
    }
    return badges.get(source, source)


def _format_markdown(
    query: str,
    articles: List[Dict[str, Any]],
    sources: List[str],
    compact: bool,
    run_time: str,
) -> str:
    lines = []
    lines.append(f"# Literature Search: {query}")
    lines.append(f"")
    lines.append(f"**Run**: {run_time}  ")
    lines.append(f"**Sources**: {', '.join(_source_badge(s) for s in sources)}  ")
    lines.append(f"**Results**: {len(articles)} unique papers after deduplication  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by source for a cleaner overview
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for art in articles:
        by_source.setdefault(art.get("source", "unknown"), []).append(art)

    lines.append("## Summary by Source")
    lines.append("")
    for src in sources:
        count = len(by_source.get(src, []))
        lines.append(f"- **{_source_badge(src)}**: {count} papers")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Results")
    lines.append("")

    for i, art in enumerate(articles, 1):
        title = art.get("title") or "No title"
        url = art.get("url", "")
        year = art.get("year", "")
        authors = art.get("authors", [])
        venue = art.get("venue", "")
        doi = art.get("doi", "")
        pdf = art.get("pdf_url", "")
        source = _source_badge(art.get("source", ""))
        citations = art.get("citation_count", 0)
        keywords = art.get("keywords", [])
        is_preprint = art.get("is_preprint", False)
        published_doi = art.get("ids", {}).get("published_doi", "")

        # Title line
        if url:
            lines.append(f"### {i}. [{title}]({url})")
        else:
            lines.append(f"### {i}. {title}")

        # Metadata line
        meta_parts = [f"**{source}**", f"*{year}*" if year else ""]
        if citations:
            meta_parts.append(f"🔖 {citations} citations")
        if is_preprint:
            if published_doi:
                meta_parts.append(f"✅ Published → [{published_doi}](https://doi.org/{published_doi})")
            else:
                meta_parts.append("📄 Preprint")
        if pdf:
            meta_parts.append(f"[PDF]({pdf})")
        lines.append(" | ".join(p for p in meta_parts if p))

        # Authors & venue
        author_str = ", ".join(authors[:6])
        if len(authors) > 6:
            author_str += f" +{len(authors) - 6} more"
        if author_str:
            lines.append(f"*{author_str}*")
        if venue:
            lines.append(f"*{venue}*")

        # DOI
        if doi:
            lines.append(f"DOI: [{doi}](https://doi.org/{doi})")

        # Keywords / MeSH / fields
        if keywords:
            lines.append(f"**Keywords**: {', '.join(keywords[:8])}")

        # Abstract
        if not compact:
            abstract = art.get("abstract", "").strip()
            if abstract:
                lines.append("")
                lines.append(f"> {abstract.replace(chr(10), chr(10) + '> ')}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slug + output path helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len]


def _output_path(output_dir: Path, query: str, ext: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    slug = _slugify(query)
    return output_dir / f"search_{slug}_{ts}.{ext}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search literature across PubMed, Semantic Scholar, bioRxiv/medRxiv "
                    "and save results to brainstorm/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Search query (e.g. 'SGLT2 proximal tubule transport model')",
    )
    parser.add_argument(
        "--queries-file",
        default=None,
        help=(
            "Path to a plain-text file with one query per line. "
            "Runs run_batch() across all queries and merges results. "
            "When set, the positional 'query' argument is ignored."
        ),
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["pubmed", "openalex", "biorxiv", "medrxiv"],
        default=["pubmed", "openalex", "biorxiv", "medrxiv"],
        help="Sources to query (default: all four)",
        metavar="SOURCE",
    )
    parser.add_argument(
        "-n", "--max-results",
        type=int,
        default=10,
        help="Max results per source (default: 10)",
    )
    parser.add_argument(
        "--year",
        default=None,
        help="Year range for OpenAlex, e.g. '2018-2024' or '2022'",
    )
    parser.add_argument(
        "--date-range",
        default="2019-01-01:2099-12-31",
        help="Date range for bioRxiv/medRxiv (YYYY-MM-DD:YYYY-MM-DD)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Omit abstracts (saves tokens; good for broad sweeps)",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Also save raw JSON alongside the Markdown report",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Output directory (default: brainstorm/ relative to repo root)",
    )
    parser.add_argument(
        "--pubmed-api-key",
        default=os.getenv("NCBI_API_KEY"),
        help="NCBI API key (or set NCBI_API_KEY)",
    )
    parser.add_argument(
        "--pubmed-email",
        default=os.getenv("NCBI_EMAIL"),
        help="NCBI contact email (or set NCBI_EMAIL)",
    )
    parser.add_argument(
        "--openalex-email",
        default=os.getenv("OPENALEX_EMAIL"),
        help="Contact email for OpenAlex's polite pool (or set OPENALEX_EMAIL)",
    )

    args = parser.parse_args()

    if not args.query and not args.queries_file:
        parser.error("Either a positional query or --queries-file must be provided.")

    # Resolve output directory
    output_dir = Path(
        args.dir
        if args.dir
        else Path(_REPO_ROOT) / "brainstorm"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- Batch mode (--queries-file) ---
    if args.queries_file:
        queries_path = Path(args.queries_file)
        if not queries_path.exists():
            sys.stderr.write(f"[ERROR] queries file not found: {queries_path}\n")
            sys.exit(1)
        queries = [
            line.strip() for line in queries_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        sys.stderr.write(f"[INFO] Batch mode: {len(queries)} queries from {queries_path}\n")
        sys.stderr.write(f"[INFO] Sources: {', '.join(args.sources)}\n")
        sys.stderr.write(f"[INFO] Max/query/source: {args.max_results}\n\n")
        articles = run_batch(
            queries=queries,
            sources=args.sources,
            max_per_query=args.max_results,
            compact=args.compact,
            year_range=args.year,
            date_range=args.date_range,
            pubmed_api_key=args.pubmed_api_key,
            pubmed_email=args.pubmed_email,
            openalex_mailto=args.openalex_email,
        )
        effective_query = f"batch:{queries_path.name}"
    # --- Single query mode ---
    else:
        sys.stderr.write(f"[INFO] Searching: {args.query!r}\n")
        sys.stderr.write(f"[INFO] Sources:   {', '.join(args.sources)}\n")
        sys.stderr.write(f"[INFO] Max/source: {args.max_results}\n\n")
        articles = search_all(
            query=args.query,
            max_results=args.max_results,
            sources=args.sources,
            compact=args.compact,
            year_range=args.year,
            date_range=args.date_range,
            pubmed_api_key=args.pubmed_api_key,
            pubmed_email=args.pubmed_email,
            openalex_mailto=args.openalex_email,
        )
        effective_query = args.query

    sys.stderr.write(f"[INFO] {len(articles)} unique papers after deduplication.\n\n")

    # --- Markdown report ---
    md = _format_markdown(
        query=effective_query,
        articles=articles,
        sources=args.sources,
        compact=args.compact,
        run_time=run_time,
    )
    md_path = _output_path(output_dir, args.query, "md")
    md_path.write_text(md, encoding="utf-8")
    sys.stderr.write(f"[INFO] Markdown saved → {md_path}\n")

    # --- Optional JSON dump ---
    if args.save_json:
        payload = {
            "query": args.query,
            "run_time": run_time,
            "sources": args.sources,
            "total_unique": len(articles),
            "articles": articles,
        }
        json_path = _output_path(output_dir, args.query, "json")
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        sys.stderr.write(f"[INFO] JSON saved   → {json_path}\n")

    # Print summary table to stdout
    print(f"\nFound {len(articles)} unique papers:\n")
    src_counts: Dict[str, int] = {}
    for a in articles:
        src_counts[a.get("source", "?")] = src_counts.get(a.get("source", "?"), 0) + 1
    for src, cnt in src_counts.items():
        print(f"  {_source_badge(src):<22} {cnt}")

    print(f"\nReport saved to: {md_path}")


if __name__ == "__main__":
    main()

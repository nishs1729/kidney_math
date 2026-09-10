"""
paper_store.py — Persistent, human-readable paper store.

Replaces the fresh timestamped JSON+MD pair produced each round with a single
append-friendly JSONL file (default `brainstorm/papers.jsonl`), one paper per
line. Not specific to any one topic — `question` is just a field on each
record, so results for different scientific questions live in the same store
without colliding.

Why JSONL over a single JSON blob or a database file: every line is a
complete, independently readable/greppable JSON object, `git diff` on the
file only ever shows the papers that actually changed, and appending a new
paper never requires rewriting or re-parenting the rest of the file.

Each record is a normalised article dict (see search_utils.py's schema) plus:
  id                str   stable key: "doi:<lowercased doi>" or "title:<alpha-only title>"
  question          str   the question/topic this paper was first retrieved for
  first_seen_round  int   which round introduced this paper to the store
  query_tags        list[str]  every taxonomy tag this paper has matched, across all rounds

Usage:
    from tool.paper_store import load, upsert, export_markdown

    store = load()
    new_ids = upsert(store, articles, question=QUESTION, round_num=1)
    export_markdown(store, "brainstorm/papers.md")
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE_PATH = _REPO_ROOT / "brainstorm" / "papers.jsonl"


def paper_id(article: Dict[str, Any]) -> str:
    """
    Stable id for a paper: DOI when present, else an alpha-only title key.

    Deliberately simpler than search_utils.deduplicate()'s fuzzy Jaccard
    passes — this only needs to recognize a paper it has already stored
    across rounds, and each round's incoming list has already been through
    deduplicate() once, so exact-key matching here is sufficient.
    """
    doi = (article.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    alpha = "".join(c for c in (article.get("title") or "").lower() if c.isalpha())
    return f"title:{alpha}"


def load(path: Path = DEFAULT_STORE_PATH) -> Dict[str, Dict[str, Any]]:
    """Load the store into a dict keyed by paper id. Empty dict if the file doesn't exist yet."""
    if not path.exists():
        return {}
    store: Dict[str, Dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            store[rec["id"]] = rec
    return store


def save(store: Dict[str, Dict[str, Any]], path: Path = DEFAULT_STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for pid in sorted(store):
            fh.write(json.dumps(store[pid], ensure_ascii=False) + "\n")


def upsert(
    store: Dict[str, Dict[str, Any]],
    articles: List[Dict[str, Any]],
    question: str,
    round_num: int,
    path: Path = DEFAULT_STORE_PATH,
) -> List[str]:
    """
    Merge a fresh batch of articles into the store (in place) and persist it.

    - A paper not previously in the store is inserted with `first_seen_round`
      and `question` set, and its `query_tag` folded into a `query_tags` list.
    - A paper already in the store keeps its accumulated fields (e.g. any
      enrichment done since); this call only unions in the new `query_tag`
      and fills fields that were previously empty (e.g. an abstract fetched
      by a later, richer source).

    Returns the list of paper ids that were newly inserted by this call —
    "genuinely new papers this round" without needing to re-read the whole
    store to figure that out.
    """
    new_ids: List[str] = []

    for art in articles:
        pid = paper_id(art)
        tag = art.get("query_tag", "")

        if pid not in store:
            rec = dict(art)
            rec["id"] = pid
            rec["question"] = question
            rec["first_seen_round"] = round_num
            rec["query_tags"] = [tag] if tag else []
            rec.pop("query_tag", None)
            store[pid] = rec
            new_ids.append(pid)
        else:
            rec = store[pid]
            if tag and tag not in rec.get("query_tags", []):
                rec.setdefault("query_tags", []).append(tag)
            # Fill gaps only — never clobber data already accumulated on the record.
            for field, value in art.items():
                if field in ("query_tag", "query_text"):
                    continue
                if not rec.get(field) and value:
                    rec[field] = value

    save(store, path)
    return new_ids


def export_markdown(
    store: Dict[str, Dict[str, Any]],
    out_path: Path,
    filter_fn: Optional[Any] = None,
) -> None:
    """Render the (optionally filtered) store as a human-readable Markdown list."""
    records = list(store.values())
    if filter_fn:
        records = [r for r in records if filter_fn(r)]
    records.sort(key=lambda r: (r.get("question", ""), -int(r.get("citation_count") or 0)))

    lines = [f"# Paper store ({len(records)} papers)\n"]
    current_question = None
    for r in records:
        if r.get("question") != current_question:
            current_question = r.get("question")
            lines.append(f"\n## {current_question}\n")
        lines.append(f"### [{r.get('title','(no title)')}]({r.get('url','')})")
        lines.append(
            f"{r.get('source','')} | {r.get('year','')} | "
            f"citations: {r.get('citation_count', 0)} | "
            f"{'review' if r.get('is_review') else ''} | "
            f"tags: `{', '.join(r.get('query_tags', []))}`"
        )
        abstract = (r.get("abstract") or "").strip()
        if abstract:
            lines.append("")
            lines.append(f"> {abstract.replace(chr(10), chr(10) + '> ')}")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

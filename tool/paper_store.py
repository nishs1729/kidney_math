"""
paper_store.py — Persistent, human-readable paper store.

Replaces the fresh timestamped JSON+MD pair produced each round with a single
append-friendly JSONL file, one paper per line. Not specific to any one
topic — `question` is just a field on each record, so results for different
scientific questions live in the same store without colliding.

Why JSONL over a single JSON blob or a database file: every line is a
complete, independently readable/greppable JSON object, `git diff` on the
file only ever shows the papers that actually changed, and appending a new
paper never requires rewriting or re-parenting the rest of the file.

brainstorm/ layout (see brainstorm/README.md):
    brainstorm/
      <question-slug>/
        README.md      <- full question text (slug alone is lossy)
        papers.md      <- human-readable export, this question only
        taxonomy/
          round_01.py
      data/
        papers.jsonl   <- the shared store itself; not meant to be read directly

Each record is a normalised article dict (see search_utils.py's schema) plus:
  id                  str   stable key: "doi:<lowercased doi>" or "title:<alpha-only title>"
  question            str   the question/topic this paper was first retrieved for
  first_seen_round    int   which round introduced this paper to the store
  query_tags          list[str]  every taxonomy tag this paper has matched, across all rounds

Optionally, once scoring has been run (see `score_papers.py` / `apply_scores.py`):
  heuristic_score     float 0-100, pure metadata (citation velocity, tag coverage, review boost)
  similarity_score    float 0-1, TF-IDF cosine similarity to the question text
  relevance_score     any   rubric-based score assigned after reading the abstract
  relevance_rationale str   one-line justification for relevance_score

And once PDFs have been fetched (see `fetch_pdfs.py`):
  pdf_path            str   path to the downloaded PDF, relative to the repo root
  pdf_status          str   "downloaded:open_access" | "downloaded:institute" | "unavailable" | "error:<msg>"

Usage:
    from tool.paper_store import load, upsert, export_question

    store = load()
    new_ids = upsert(store, articles, question=QUESTION, round_num=1)
    export_question(store, QUESTION)   # writes <slug>/README.md + <slug>/papers.md
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
BRAINSTORM_ROOT = _REPO_ROOT / "brainstorm"
DEFAULT_STORE_PATH = BRAINSTORM_ROOT / "data" / "papers.jsonl"


def slugify(text: str, max_len: int = 40) -> str:
    """
    Naive kebab-case identifier: lowercases, replaces non-alphanumerics with
    '-', and truncates. This is a dumb fallback, not a summarizer — truncating
    a full question sentence usually just keeps its generic opening words
    ("what-mathematical-models-have-been-used...") rather than the actual
    topic. Prefer passing an explicit, hand-picked slug (e.g.
    "tgf-renal-autoregulation") wherever one is accepted; this is only used
    when no explicit slug is given.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def question_dir(
    question: str,
    slug: Optional[str] = None,
    brainstorm_root: Path = BRAINSTORM_ROOT,
) -> Path:
    """The brainstorm/<slug>/ folder for a given question. Pass an explicit `slug` if you have one."""
    return brainstorm_root / (slug or slugify(question))


def safe_filename(paper_id_: str, ext: str) -> str:
    """
    Deterministic, filesystem-safe filename for a paper id (e.g.
    'doi:10.1234/abc' -> 'doi_10.1234_abc.pdf'). Shared by `fetch_pdfs.py`
    (pdfs/) and `link_summaries.py` (summaries/) so both artifact kinds for
    the same paper are trivially cross-referenceable by filename alone.
    """
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", paper_id_)
    return f"{stem}{ext}"


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
    slug: Optional[str] = None,
    path: Path = DEFAULT_STORE_PATH,
) -> List[str]:
    """
    Merge a fresh batch of articles into the store (in place) and persist it.

    - A paper not previously in the store is inserted with `first_seen_round`
      and `question` set, and its `query_tag` folded into a `query_tags` list.
      The resolved `slug` (explicit if given, else naively derived — see
      `slugify()`) is stored too, so a later `export_question()` call (e.g.
      from enrich_abstracts.py, which doesn't necessarily know what slug a
      question was first run with) lands in the same brainstorm/<slug>/
      folder instead of silently creating a second one.
    - A paper already in the store keeps its accumulated fields (e.g. any
      enrichment done since); this call only unions in the new `query_tag`
      and fills fields that were previously empty (e.g. an abstract fetched
      by a later, richer source).

    Returns the list of paper ids that were newly inserted by this call —
    "genuinely new papers this round" without needing to re-read the whole
    store to figure that out.
    """
    new_ids: List[str] = []
    resolved_slug = slug or slugify(question)

    for art in articles:
        pid = paper_id(art)
        tag = art.get("query_tag", "")

        if pid not in store:
            rec = dict(art)
            rec["id"] = pid
            rec["question"] = question
            rec["slug"] = resolved_slug
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


def _rank_value(r: Dict[str, Any]) -> float:
    """
    Best-available ranking signal, in order of trust: a rubric-based
    `relevance_score` (scaled up so any scored paper outranks any unscored
    one) beats the cheap `heuristic_score`/`similarity_score` combo, which
    beats a bare citation count for stores that haven't been scored at all.
    """
    relevance = r.get("relevance_score")
    if relevance is not None:
        return 1_000_000 + float(relevance)
    heuristic = r.get("heuristic_score")
    similarity = r.get("similarity_score")
    if heuristic is not None or similarity is not None:
        return (heuristic or 0.0) + (similarity or 0.0) * 100
    return float(r.get("citation_count") or 0)


def export_markdown(
    store: Dict[str, Dict[str, Any]],
    out_path: Path,
    filter_fn: Optional[Any] = None,
) -> None:
    """Render the (optionally filtered) store as a human-readable Markdown list."""
    records = list(store.values())
    if filter_fn:
        records = [r for r in records if filter_fn(r)]
    records.sort(key=lambda r: (r.get("question", ""), -_rank_value(r)))

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
        score_bits = []
        if r.get("relevance_score") is not None:
            score_bits.append(f"relevance: {r['relevance_score']}")
        if r.get("heuristic_score") is not None:
            score_bits.append(f"heuristic: {r['heuristic_score']}")
        if r.get("similarity_score") is not None:
            score_bits.append(f"similarity: {r['similarity_score']}")
        if r.get("pdf_status"):
            score_bits.append(f"pdf: {r['pdf_status']}")
        if score_bits:
            lines.append(" | ".join(score_bits))
        if r.get("relevance_rationale"):
            lines.append(f"*{r['relevance_rationale']}*")
        abstract = (r.get("abstract") or "").strip()
        if abstract:
            lines.append("")
            lines.append(f"> {abstract.replace(chr(10), chr(10) + '> ')}")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def export_question(
    store: Dict[str, Dict[str, Any]],
    question: str,
    slug: Optional[str] = None,
    brainstorm_root: Path = BRAINSTORM_ROOT,
) -> Path:
    """
    Write brainstorm/<slug>/README.md (the question text) and
    brainstorm/<slug>/papers.md (this question's papers only).

    This is the human-facing output of a round — everything else (the raw
    store, the taxonomy specs that produced it) is supporting material.

    Returns the question's folder path.
    """
    qdir = question_dir(question, slug, brainstorm_root)
    qdir.mkdir(parents=True, exist_ok=True)

    readme = qdir / "README.md"
    readme.write_text(f"# {question}\n", encoding="utf-8")

    export_markdown(
        store,
        qdir / "papers.md",
        filter_fn=lambda r: r.get("question") == question,
    )
    return qdir

# Literature Search Tools

Zero-dependency Python tools implementing Steps 1–11 of the search loop
(`process.md`, `instructions.md`): querying **PubMed**, **OpenAlex**, and
**bioRxiv/medRxiv** preprints, deduplicating, scoring, and fetching PDFs into
a durable paper store per question. Designed for AI agents conducting
literature surveys — mechanical work (search, dedup, ranking, downloads)
happens in Python; judgment calls (what's actually relevant) stay with
whoever is reading the abstracts.

## Files

| File | Purpose |
|---|---|
| `search_utils.py` | All search functions — single import point |
| `query_utils.py` | Query linting (`lint_query`) + taxonomy-grid query building (`build_queries`) |
| `paper_store.py` | Durable JSONL paper store, keyed by question, with Markdown export |
| `run_loop.py` | CLI runner — Steps 1–4 end-to-end, writes into the paper store |
| `enrich_abstracts.py` | Follow-up pass: fetches abstracts for store records that don't have one |
| `fetch_pdfs.py` | CLI: downloads PDFs into `brainstorm/<slug>/pdfs/` — open access first, then an institute-network fallback, then a manual-placement check |
| `link_summaries.py` | CLI: scans `brainstorm/<slug>/summaries/` and registers written summary files back into the store |
| `scoring.py` | Zero-token relevance signals: `heuristic_score` (metadata) + `similarity_scores` (TF-IDF vs. the question) |
| `score_papers.py` | CLI: computes/stores the above for a question, prints a ranked table |
| `apply_scores.py` | CLI: persists rubric-based `relevance_score`/`relevance_rationale` (from reading each paper's summary) into the store |
| `triage_papers.py` | CLI: buckets papers into `status` core/borderline/excluded from `relevance_score`, writes `core_papers.md` |
| `expand_citations.py` | CLI: pulls backward references + forward citations for core-tier papers via OpenAlex |
| `round_report.py` | CLI: per-round and per-tag yield report, plus a saturation verdict |

## Output layout

Results land in `brainstorm/` (see `brainstorm/README.md`):

```
brainstorm/
├── <question-slug>/
│   ├── README.md        <- the question text
│   ├── papers.md         <- human-readable results for this question
│   ├── pdfs/
│   │   ├── *.pdf                        <- downloaded full texts
│   │   └── _manual_download_needed.md   <- papers that need manual retrieval
│   ├── summaries/
│   │   └── *.md                         <- one structured summary per paper
│   ├── core_papers.md    <- just the status:core tier (from triage_papers.py)
│   └── taxonomy/
│       ├── round_01.py   <- Step-2 query spec, saved for provenance
│       └── round_02.py   <- refined each loop iteration (Step 10)
└── data/
    └── papers.jsonl      <- the shared store (all questions)
```

---

## Quick Start

### 1. Write a taxonomy spec for a round

`brainstorm/<slug>/taxonomy/round_01.py`:

```python
ROWS = [
    {"tag": "TGF", "or_terms": ['"tubuloglomerular feedback"', '"macula densa signal"']},
    {"tag": "autoregulation", "or_terms": ['"renal autoregulation"']},
]
COLUMNS = {
    "model": '("mathematical model" OR "computational model")',
}
EXTRA_AND = "kidney"
```

A `.py` file rather than JSON so query phrases can use plain double quotes
without escaping. `build_queries()` lints every generated query before
returning (catches unbalanced quotes/parens, a quoted phrase directly
followed by a bare word with no AND/OR/NOT, and ambiguous bare abbreviations
like a standalone `TGF`) and raises with specifics instead of silently
running a broken query.

### 2. Run the round

```bash
python tool/run_loop.py \
    --question "What mathematical models have been used to study tubuloglomerular feedback and its role in renal autoregulation?" \
    --slug tgf-renal-autoregulation \
    --round 1
```

Prints a summary (source/tag counts, how many papers were genuinely new vs.
already in the store) and refreshes `brainstorm/<slug>/papers.md`.
Re-running the same `--question`/`--slug` later only reports new papers; a
different question's papers never collide with this one.

### 3. Fetch abstracts

`run_loop.py` fetches `compact=True` (no abstracts) for a fast broad sweep.
Follow up with:

```bash
python tool/enrich_abstracts.py
```

Batches PubMed lookups by PMID and looks up bioRxiv/medRxiv abstracts by DOI
via Europe PMC, then refreshes every affected question's `papers.md`.

### 4. Fetch PDFs

```bash
python tool/fetch_pdfs.py --question "..." --slug "..." [--limit N]
```

Tries a file already at the expected path (see below), then an open-access
`pdf_url`/Unpaywall lookup, then a `citation_pdf_url` meta-tag fetch off the
DOI's landing page (works for subscribed content only if the current network
is recognized by the publisher, e.g. an institute connection — never
attempts to defeat a paywall). Every download is verified to actually be a
PDF before being kept. Failures land in
`brainstorm/<slug>/pdfs/_manual_download_needed.md`, each with the exact
filename to save a manually-obtained copy as; save it there and re-run the
same command (without `--force`) to have it picked up automatically as
`pdf_status: downloaded:manual`.

### 5. Summarize papers

For each paper (full-text PDF if available, abstract otherwise), write a
structured Markdown summary to `brainstorm/<slug>/summaries/<file>.md` (see
`instructions.md` Step 6 for the template and filename convention), then
register the batch:

```bash
python tool/link_summaries.py --question "..." --slug "..."
```

Sets `summary_path`/`summary_status` on matching records, refreshes
`papers.md`, and reports how many summaries are written vs. still missing.

### 6. Score for relevance

```bash
python tool/score_papers.py --question "..." --slug "..."
```

Writes `heuristic_score` (0–100: citation velocity, taxonomy tag coverage,
review boost) and `similarity_score` (0–1: TF-IDF cosine similarity to the
question — lexical, not semantic) onto every paper, and prints a ranked
table. Neither reads the argument of the paper — they're for prioritizing
review order, not a verdict.

After reading each paper's summary (from step 5) and forming a rubric-based
judgment, persist it:

```bash
python tool/apply_scores.py scores.json --question "..." --slug "..."
```

where `scores.json` is `{paper_id: {"relevance_score": 0-5, "relevance_rationale": "..."}}`.
This is the score that then drives `papers.md`'s sort order.

### 7. Triage, expand citations, refresh, repeat

```bash
python tool/triage_papers.py --question "..." --slug "..."       # status: core/borderline/excluded
python tool/expand_citations.py --question "..." --slug "..." --round 2   # citation graph off the core tier
python tool/round_report.py --question "..." --slug "..." --by-tag        # which taxonomy cells/rounds paid off
```

`expand_citations.py` pulls backward references and forward citations (via
OpenAlex) for every `status: core` paper and adds new candidates at the
given round — the highest-yield way to find relevant work that keyword
search structurally can't. Its new papers loop back through steps 3–6
(PDF/summarize/score) before another round can triage/expand from them.
`round_report.py`'s `--by-tag` table plus the *Gaps* sections in Step 5's
summaries are the inputs for writing the next round's taxonomy (back to
step 1); its plain (no `--by-tag`) form reports a `SATURATED` verdict once
new rounds stop contributing core papers. See `instructions.md` Steps 8–11
for the full loop.

---

## Python API (`search_utils.py`)

```python
from tool.search_utils import search_all, deduplicate
from tool.search_utils import search_pubmed, search_openalex, search_biorxiv, search_medrxiv

# Query all sources at once (deduped)
articles = search_all("SGLT2 proximal tubule", max_results=10)

# Single source
pm_hits = search_pubmed("glomerular filtration rate model", max_results=10)
oa_hits = search_openalex("renal hemodynamics", year_range="2018-2024")
biorxiv_hits = search_biorxiv("nephron transport", max_results=5)

# Deduplicate a mixed list
unique = deduplicate(pm_hits + oa_hits + biorxiv_hits)
```

### Unified article schema

Every function returns the same dict shape:

```
source          'pubmed' | 'openalex' | 'biorxiv' | 'medrxiv'
title           str
authors         list[str]
year            str
pub_date        str
venue           str
abstract        str
doi             str
url             str
pdf_url         str   (open-access PDF if available)
ids             dict  {pmid, openalex_id, …}
citation_count  int   (OpenAlex only)
keywords        list[str]   MeSH terms or OpenAlex concepts
pub_types       list[str]   publication types (PubMed/Europe PMC only)
is_review       bool        derived from pub_types (PubMed/Europe PMC only)
is_preprint     bool
referenced_works    list[str]   OpenAlex short IDs this paper cites (OpenAlex only)
cited_by_api_url    str         ready-made OpenAlex URL for this paper's citing works (OpenAlex only)
```

---

## Environment Variables (optional)

| Variable | Effect |
|---|---|
| `NCBI_API_KEY` | Raises NCBI rate limit from 3 → 10 req/s |
| `NCBI_EMAIL` | Contact email for NCBI API compliance |
| `OPENALEX_EMAIL` | Moves OpenAlex requests into the faster "polite pool" |

# Literature Search Tools

Zero-dependency Python tools implementing Steps 1–4 of the search loop
(`process.md`, `instructions.md`): querying **PubMed**, **OpenAlex**, and
**bioRxiv/medRxiv** preprints, deduplicating, and building up a durable paper
store per question. Designed for AI agents conducting literature surveys
with minimal token consumption.

## Files

| File | Purpose |
|---|---|
| `search_utils.py` | All search functions — single import point |
| `query_utils.py` | Query linting (`lint_query`) + taxonomy-grid query building (`build_queries`) |
| `paper_store.py` | Durable JSONL paper store, keyed by question, with Markdown export |
| `run_loop.py` | CLI runner — Steps 1–4 end-to-end, writes into the paper store |
| `enrich_abstracts.py` | Follow-up pass: fetches abstracts for store records that don't have one |

## Output layout

Results land in `brainstorm/` (see `brainstorm/README.md`):

```
brainstorm/
├── <question-slug>/
│   ├── README.md        <- the question text
│   ├── papers.md         <- human-readable results for this question
│   └── taxonomy/
│       └── round_01.py   <- Step-2 query spec, saved for provenance
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
```

---

## Environment Variables (optional)

| Variable | Effect |
|---|---|
| `NCBI_API_KEY` | Raises NCBI rate limit from 3 → 10 req/s |
| `NCBI_EMAIL` | Contact email for NCBI API compliance |
| `OPENALEX_EMAIL` | Moves OpenAlex requests into the faster "polite pool" |

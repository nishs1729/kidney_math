# Literature Search Tools

Zero-dependency Python tools for querying **PubMed**, **Semantic Scholar**, and **bioRxiv/medRxiv** preprints. Designed for AI agents conducting literature surveys with minimal token consumption.

## Files

| File | Purpose |
|---|---|
| `search_utils.py` | All search functions — single import point |
| `run_search.py` | CLI runner — searches, deduplicates, saves to `brainstorm/` |

---

## Quick Start

### Run a search and save to `brainstorm/`

```bash
python tool/run_search.py "SGLT2 kidney tubule transport"
```

Saves `brainstorm/search_sglt2_kidney_<timestamp>.md` automatically.

### Common options

```bash
# Restrict sources, filter by year
python tool/run_search.py "GFR mathematical model" \
    --sources pubmed semantic_scholar --year 2018-2024 -n 15

# Omit abstracts for a fast broad sweep
python tool/run_search.py "CKD biomarkers" --compact -n 30

# Also save raw JSON
python tool/run_search.py "renal hemodynamics autoregulation" --save-json

# Custom output directory
python tool/run_search.py "podocyte injury model" --dir /path/to/dir

# medRxiv only
python tool/run_search.py "chronic kidney disease" --sources medrxiv
```

---

## Python API (`search_utils.py`)

```python
from tool.search_utils import search_all, deduplicate
from tool.search_utils import search_pubmed, search_semantic_scholar
from tool.search_utils import search_biorxiv, search_medrxiv

# Query all sources at once (deduped)
articles = search_all("SGLT2 proximal tubule", max_results=10)

# Single source
pm_hits = search_pubmed("glomerular filtration rate model", max_results=10)
s2_hits = search_semantic_scholar("renal hemodynamics", year_range="2018-2024")
biorxiv_hits = search_biorxiv("nephron transport", max_results=5)

# Deduplicate a mixed list
unique = deduplicate(pm_hits + s2_hits + biorxiv_hits)
```

### Unified article schema

Every function returns the same dict shape:

```
source          'pubmed' | 'semantic_scholar' | 'biorxiv' | 'medrxiv'
title           str
authors         list[str]
year            str
pub_date        str
venue           str
abstract        str
doi             str
url             str
pdf_url         str   (open-access PDF if available)
ids             dict  {pmid, arxiv_id, paper_id, …}
citation_count  int   (Semantic Scholar only)
keywords        list[str]   MeSH terms or fields-of-study
is_preprint     bool
```

---

## Environment Variables (optional)

| Variable | Effect |
|---|---|
| `NCBI_API_KEY` | Raises NCBI rate limit from 3 → 10 req/s |
| `NCBI_EMAIL` | Contact email for NCBI API compliance |
| `S2_API_KEY` | Raises Semantic Scholar rate limit |

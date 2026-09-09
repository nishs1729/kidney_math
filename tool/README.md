# PubMed Query Tool for AI Agents

A zero-dependency Python tool to query PubMed via NCBI E-utilities. Designed for AI agents conducting literature reviews with minimal token consumption.

## Features

- **Zero Third-Party Dependencies**: Pure Python standard library (`urllib`, `xml.etree.ElementTree`, `json`, `argparse`). Works in any standard Python 3.7+ environment without `pip install`.
- **Low Token Consumption**:
  - `--compact`: Omits abstracts to provide high-density summaries (PMID, title, authors, journal, year, DOI) for scanning large sets of candidate papers.
  - `--format json` or `--format markdown`: Clean machine-readable JSON or formatted Markdown.
  - `--output <file>`: Direct write to disk to avoid polluting LLM context window.
- **Rich Data**: When full details are requested, extracts full structured abstracts, author lists, DOIs, PMCs, and MeSH terms/keywords.
- **Resilient**: Built-in exponential backoff for NCBI rate limits (HTTP 429/500/503).
- **Dual Interface**: Use as a CLI tool or import directly into Python scripts.

---

## CLI Usage

### 1. Broad survey (low token usage)
```bash
python tool/pubmed_query.py "renal hemodynamics mathematical model" --max-results 5 --compact
```

### 2. Detailed search with abstracts (JSON format)
```bash
python tool/pubmed_query.py "SGLT2 inhibitors kidney physiology" --max-results 3 --format json
```

### 3. Markdown output for LLM prompts
```bash
python tool/pubmed_query.py "glomerular filtration barrier" --max-results 3 --format markdown
```

### 4. Fetch specific PMIDs directly
```bash
python tool/pubmed_query.py --pmids 33814424,32192931 --format markdown
```

### 5. Save directly to file (0 context tokens used)
```bash
python tool/pubmed_query.py "nephron transport model" --max-results 20 --output literature.json
```

---

## Python Module Usage (Direct Package Import)

No `pip install -e .` is required. You can directly import from `tool` in your scripts:

```python
from tool import query_pubmed, fetch_details, search_pubmed
# Or:
from tool.pubmed_query import query_pubmed


# Full query with abstracts
data = query_pubmed("glomerulotubular balance", max_results=5)

for article in data["articles"]:
    print(f"PMID: {article['pmid']}")
    print(f"Title: {article['title']}")
    print(f"Journal: {article['journal']} ({article['year']})")
    print(f"DOI: {article['doi']}")
    print(f"Abstract:\n{article.get('abstract', '')}\n")

# Compact query (omits abstracts to minimize memory/tokens)
compact_data = query_pubmed("podocyte injury", max_results=10, compact=True)
```

---

## Environment Variables (Optional)

- `NCBI_API_KEY`: Increases rate limit from 3 requests/sec to 10 requests/sec.
- `NCBI_EMAIL`: Contact email passed to NCBI for API compliance.

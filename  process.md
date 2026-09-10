## Info repository

- database (json/xml) for keeping track of the literature and referenes



## Tools

- [PubMed Query Tool](file:///home/nishant/Research/kidney_math/tool/pubmed_query.py): Minimal, zero-dependency script to query PubMed API with low token consumption (JSON/Markdown, compact mode). 


## Search loop

1. **Decompose the question into concepts** — generate free-text concepts via LLM, and cross-check them against PubMed's MeSH database to catch domain terminology the LLM might miss.

2. **Generate a stratified query batch** — 8–12 queries per round, each tied to a specific cell of your topic taxonomy (e.g., subsystem × modeling approach × disease context), rather than a large batch generated all at once up front.

3. **Retrieve from multiple sources** — PubMed, Semantic Scholar, and preprint servers (bioRxiv/medRxiv), since math-modeling work is often under-indexed in PubMed alone.

4. **Deduplicate** — using fuzzy title/author matching or embedding similarity, not just exact DOI/PMID matches, to catch preprint-vs-published duplicates.

5. **Screen in two stages** — a cheap, permissive pass on title/abstract, then a stricter rubric-based pass on survivors, logging exclusion reasons so nothing gets re-screened later.

6. **Score and prioritize** — rank surviving papers by citation count, recency, and density of gap-relevant claims; this score decides which get full-text extraction versus abstract-only treatment.

7. **Extract structured fields** — methods, modeling approach, explicit/implied gap statements, and any new terminology encountered.

8. **Expand citations bidirectionally** — pull backward references (foundational work), forward citations (who's since cited it), and co-citation clusters (related papers with no shared keywords) from the highest-priority papers.

9. **Refresh concepts** — fold newly discovered terminology and entities from extraction and citation expansion back into the concept set.

10. **Loop back to step 2** — generate the next query batch from the refreshed concepts.

11. **Stop on saturation** — end the loop when new, non-duplicate relevant papers per round fall below a threshold for two consecutive rounds.
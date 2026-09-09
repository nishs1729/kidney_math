## Info repository

- database (json/xml) for keeping track of the literature and referenes



## Tools

- [PubMed Query Tool](file:///home/nishant/Research/kidney_math/tool/pubmed_query.py): Minimal, zero-dependency script to query PubMed API with low token consumption (JSON/Markdown, compact mode). 


## Search loop

```
question
   ↓
generate concepts
   ↓
generate 10–30 PubMed queries
   ↓
retrieve papers
   ↓
deduplicate
   ↓
LLM relevance screening
   ↓
identify important papers
   ↓
extract terminology
   ↓
generate new searches
   ↓
citation expansion
   ↓
repeat
   ↓
stop when marginal discovery becomes small
```

Use of MeSH terms

```
free-text terms
+
MeSH terms
+
synonyms
+
older terminology
+
abbreviations
```
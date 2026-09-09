"""Literature search utilities — single import point for all sources."""
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from .search_utils import (
    search_pubmed,
    search_semantic_scholar,
    search_biorxiv,
    search_medrxiv,
    search_all,
    deduplicate,
    fetch_pubmed_by_pmids,
    fetch_s2_by_ids,
    extract_dois,
    extract_titles,
)

__all__ = [
    "search_pubmed",
    "search_semantic_scholar",
    "search_biorxiv",
    "search_medrxiv",
    "search_all",
    "deduplicate",
    "fetch_pubmed_by_pmids",
    "fetch_s2_by_ids",
    "extract_dois",
    "extract_titles",
]

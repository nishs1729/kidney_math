"""PubMed query and literature survey tools."""
import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'tool' is always importable directly
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from .pubmed_query import fetch_details, query_pubmed, search_pubmed

__all__ = ["query_pubmed", "search_pubmed", "fetch_details"]


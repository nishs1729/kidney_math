#!/usr/bin/env python3
"""
PubMed Query Tool for Literature Surveys.

A minimal, zero-dependency Python script designed for AI agents to query PubMed
via the NCBI E-utilities API. Supports token-efficient output (compact modes,
JSON, Markdown), rate limiting with backoff, and full abstract extraction.

Usage (CLI):
  python tool/pubmed_query.py "renal hemodynamics mathematical model" --max-results 5
  python tool/pubmed_query.py "SGLT2 inhibitors kidney" --compact --format json
  python tool/pubmed_query.py --pmid 34567890,34567891 --format markdown

Usage (Python module):
  from tool.pubmed_query import query_pubmed
  articles = query_pubmed("glomerular filtration rate", max_results=5, compact=True)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path so 'tool' can always be imported directly
# as a package without requiring 'pip install -e .'
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_TOOL_NAME = "ai_literature_survey_agent"
USER_AGENT = "PubMedQueryTool/1.0 (agent-literature-survey)"


def _make_request(
    endpoint: str,
    params: Dict[str, Any],
    max_retries: int = 3,
    backoff_factor: float = 1.0,
) -> bytes:
    """Make HTTP GET request to NCBI E-utilities with exponential backoff for rate limits."""
    query_string = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{NCBI_BASE_URL}/{endpoint}?{query_string}"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            # 429 = Too Many Requests, 500/502/503/504 = Server transient errors
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                sleep_time = backoff_factor * (2**attempt)
                sys.stderr.write(
                    f"[WARN] HTTP {e.code} from NCBI. Retrying in {sleep_time:.1f}s...\n"
                )
                time.sleep(sleep_time)
            else:
                sys.stderr.write(f"[ERROR] NCBI request failed ({url}): {e}\n")
                raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                sleep_time = backoff_factor * (2**attempt)
                sys.stderr.write(f"[WARN] Connection error: {e}. Retrying in {sleep_time:.1f}s...\n")
                time.sleep(sleep_time)
            else:
                sys.stderr.write(f"[ERROR] Network error contacting NCBI: {e}\n")
                raise

    raise RuntimeError("Failed to fetch data from NCBI after retries.")


def search_pubmed(
    query: str,
    max_results: int = 10,
    retstart: int = 0,
    sort: str = "relevance",
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Search PubMed using esearch.fcgi and return matching PMIDs and hit count."""
    api_key = api_key or os.getenv("NCBI_API_KEY")
    email = email or os.getenv("NCBI_EMAIL")

    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": max_results,
        "retstart": retstart,
        "sort": sort,
        "tool": DEFAULT_TOOL_NAME,
        "email": email,
        "api_key": api_key,
    }

    raw_data = _make_request("esearch.fcgi", params)
    data = json.loads(raw_data.decode("utf-8"))
    search_result = data.get("esearchresult", {})

    return {
        "count": int(search_result.get("count", 0)),
        "retmax": int(search_result.get("retmax", 0)),
        "retstart": int(search_result.get("retstart", 0)),
        "pmids": search_result.get("idlist", []),
    }


def _clean_text(elem: Optional[ET.Element]) -> str:
    """Extract and normalize all text inside an XML element, stripping tags."""
    if elem is None:
        return ""
    text = "".join(elem.itertext())
    return " ".join(text.split()).strip()


def _parse_abstract(article_elem: ET.Element) -> str:
    """Parse abstract text, preserving section headers if structured."""
    abstract_elem = article_elem.find(".//Abstract")
    if abstract_elem is None:
        return ""

    abstract_sections = []
    text_elements = abstract_elem.findall("AbstractText")

    if not text_elements:
        return _clean_text(abstract_elem)

    is_structured = any(
        el.get("Label") or el.get("NlmCategory") for el in text_elements
    )

    for el in text_elements:
        label = el.get("Label") or el.get("NlmCategory")
        section_text = _clean_text(el)
        if not section_text:
            continue
        if is_structured and label:
            abstract_sections.append(f"{label.upper()}: {section_text}")
        else:
            abstract_sections.append(section_text)

    return "\n\n".join(abstract_sections)


def _parse_authors(article_elem: ET.Element) -> List[str]:
    """Extract author list formatted as 'LastName Initials' or collective names."""
    authors = []
    for author in article_elem.findall(".//AuthorList/Author"):
        last_name = _clean_text(author.find("LastName"))
        fore_name = _clean_text(author.find("ForeName"))
        initials = _clean_text(author.find("Initials"))
        collective = _clean_text(author.find("CollectiveName"))

        if collective:
            authors.append(collective)
        elif last_name and (initials or fore_name):
            disp = f"{last_name} {initials or fore_name}"
            authors.append(disp)
        elif last_name:
            authors.append(last_name)

    return authors


def _parse_pub_date(article_elem: ET.Element) -> Dict[str, str]:
    """Extract year and full publication date string."""
    pub_date_elem = article_elem.find(".//JournalIssue/PubDate")
    year = ""
    date_parts = []

    if pub_date_elem is not None:
        year_elem = pub_date_elem.find("Year")
        if year_elem is not None:
            year = _clean_text(year_elem)
            date_parts.append(year)
        month = _clean_text(pub_date_elem.find("Month"))
        if month:
            date_parts.append(month)
        day = _clean_text(pub_date_elem.find("Day"))
        if day:
            date_parts.append(day)

        if not year:
            medline_date = _clean_text(pub_date_elem.find("MedlineDate"))
            if medline_date:
                date_parts.append(medline_date)
                # First 4 consecutive digits often represent year
                for token in medline_date.split():
                    if len(token) == 4 and token.isdigit():
                        year = token
                        break

    # Fallback to Journal Article History or DateCompleted
    if not year:
        date_completed = article_elem.find(".//DateCompleted/Year")
        if date_completed is not None:
            year = _clean_text(date_completed)

    return {"year": year, "pub_date": " ".join(date_parts) if date_parts else year}


def fetch_details(
    pmids: List[str],
    include_abstract: bool = True,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch article details and abstracts for given PMIDs using efetch.fcgi."""
    if not pmids:
        return []

    api_key = api_key or os.getenv("NCBI_API_KEY")
    email = email or os.getenv("NCBI_EMAIL")

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": DEFAULT_TOOL_NAME,
        "email": email,
        "api_key": api_key,
    }

    raw_xml = _make_request("efetch.fcgi", params)
    root = ET.fromstring(raw_xml)

    results: List[Dict[str, Any]] = []

    # Handle standard PubmedArticle elements
    for article_node in root.findall(".//PubmedArticle"):
        medline = article_node.find("MedlineCitation")
        if medline is None:
            continue

        pmid = _clean_text(medline.find("PMID"))
        article = medline.find("Article") or ET.Element("Article")
        title = _clean_text(article.find("ArticleTitle"))

        # Journal info
        journal_title = _clean_text(article.find(".//Journal/Title"))
        journal_iso = _clean_text(article.find(".//Journal/ISOAbbreviation"))
        journal = journal_iso or journal_title

        date_info = _parse_pub_date(article)
        authors = _parse_authors(article)

        # DOIs & PMC
        doi = ""
        pmc = ""
        for article_id in article_node.findall(".//PubmedData/ArticleIdList/ArticleId"):
            id_type = article_id.get("IdType")
            if id_type == "doi" and not doi:
                doi = _clean_text(article_id)
            elif id_type == "pmc" and not pmc:
                pmc = _clean_text(article_id)

        # Fallback DOI in Article
        if not doi:
            for eloc in article.findall(".//ELocationID"):
                if eloc.get("EIdType") == "doi":
                    doi = _clean_text(eloc)
                    break

        item: Dict[str, Any] = {
            "pmid": pmid,
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": date_info["year"],
            "pub_date": date_info["pub_date"],
            "doi": doi,
            "pmc": pmc,
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }

        if include_abstract:
            item["abstract"] = _parse_abstract(article)

            # MeSH headings / keywords
            mesh_terms = []
            for mesh in medline.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
                term = _clean_text(mesh)
                if term:
                    mesh_terms.append(term)
            item["mesh_terms"] = mesh_terms

        results.append(item)

    # Also handle PubmedBookArticle if any are returned
    for book_node in root.findall(".//PubmedBookArticle"):
        book_doc = book_node.find("BookDocument")
        if book_doc is None:
            continue
        pmid = _clean_text(book_doc.find("PMID"))
        title = _clean_text(book_doc.find("ArticleTitle")) or _clean_text(
            book_doc.find(".//Book/BookTitle")
        )
        authors = _parse_authors(book_doc)
        doi = ""
        for article_id in book_node.findall(".//ArticleIdList/ArticleId"):
            if article_id.get("IdType") == "doi":
                doi = _clean_text(article_id)

        item = {
            "pmid": pmid,
            "title": title,
            "authors": authors,
            "journal": _clean_text(book_doc.find(".//Book/BookTitle")),
            "year": _clean_text(book_doc.find(".//Book/PubDate/Year")),
            "pub_date": _clean_text(book_doc.find(".//Book/PubDate/Year")),
            "doi": doi,
            "pmc": "",
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
        if include_abstract:
            item["abstract"] = _parse_abstract(book_doc)
            item["mesh_terms"] = []
        results.append(item)

    return results


def query_pubmed(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    compact: bool = False,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search PubMed and fetch details in one step.

    Returns:
      {
        "query": str,
        "total_hits": int,
        "count_returned": int,
        "articles": List[Dict[str, Any]]
      }
    """
    search_res = search_pubmed(
        query=query,
        max_results=max_results,
        sort=sort,
        api_key=api_key,
        email=email,
    )
    pmids = search_res["pmids"]
    articles = fetch_details(
        pmids=pmids,
        include_abstract=not compact,
        api_key=api_key,
        email=email,
    )

    return {
        "query": query,
        "total_hits": search_res["count"],
        "count_returned": len(articles),
        "articles": articles,
    }


def format_as_markdown(data: Dict[str, Any], compact: bool = False) -> str:
    """Format query results as clean, agent-readable markdown."""
    lines = []
    lines.append(f"## PubMed Search: \"{data.get('query', '')}\"")
    lines.append(
        f"*Total hits: {data.get('total_hits', 0)} | Returned: {data.get('count_returned', 0)}*\n"
    )

    articles = data.get("articles", [])
    if not articles:
        lines.append("No results found.")
        return "\n".join(lines)

    for i, art in enumerate(articles, 1):
        pmid = art.get("pmid", "")
        title = art.get("title", "No title")
        authors = ", ".join(art.get("authors", [])) or "Unknown authors"
        journal = art.get("journal", "Unknown journal")
        year = art.get("year", "")
        doi = art.get("doi", "")
        url = art.get("pubmed_url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

        if compact:
            meta = f"({year})" if year else ""
            doi_str = f" | DOI: {doi}" if doi else ""
            lines.append(
                f"{i}. [{title}]({url}) {meta}\n"
                f"   - Authors: {authors}\n"
                f"   - Journal: {journal} | PMID: `{pmid}`{doi_str}\n"
            )
        else:
            lines.append(f"### {i}. [{title}]({url})")
            doi_part = f" | **DOI**: [{doi}](https://doi.org/{doi})" if doi else ""
            lines.append(f"- **PMID**: `{pmid}`{doi_part}")
            lines.append(f"- **Authors**: {authors}")
            lines.append(f"- **Journal**: {journal} ({year})")

            mesh = art.get("mesh_terms", [])
            if mesh:
                lines.append(f"- **Keywords/MeSH**: {', '.join(mesh[:6])}")

            abstract = art.get("abstract", "").strip()
            if abstract:
                lines.append(f"\n**Abstract**:\n> {abstract.replace(chr(10), chr(10) + '> ')}\n")
            else:
                lines.append("\n*No abstract available.*\n")
            lines.append("---")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Query PubMed API for AI agents and literature surveys."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Search query terms (e.g. 'renal hemodynamics mathematical model')",
    )
    parser.add_argument(
        "--pmids",
        help="Comma-separated PMIDs to fetch directly (bypasses search query)",
    )
    parser.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=10,
        help="Maximum articles to return (default: 10)",
    )
    parser.add_argument(
        "-s",
        "--sort",
        default="relevance",
        choices=["relevance", "pub_date"],
        help="Sort order for search results (default: relevance)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Omit abstracts to save tokens during broad surveys",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format: json (default) or markdown",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation spaces (default: 2; use 0 for single-line)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Path to write output to file instead of stdout",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("NCBI_API_KEY"),
        help="NCBI API key (or set NCBI_API_KEY env var)",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("NCBI_EMAIL"),
        help="Contact email (or set NCBI_EMAIL env var)",
    )

    args = parser.parse_args()

    if not args.query and not args.pmids:
        parser.error("Either a search query or --pmids must be provided.")

    try:
        if args.pmids:
            pmid_list = [p.strip() for p in args.pmids.split(",") if p.strip()]
            articles = fetch_details(
                pmids=pmid_list,
                include_abstract=not args.compact,
                api_key=args.api_key,
                email=args.email,
            )
            result = {
                "query": f"PMIDs: {', '.join(pmid_list)}",
                "total_hits": len(pmid_list),
                "count_returned": len(articles),
                "articles": articles,
            }
        else:
            result = query_pubmed(
                query=args.query,
                max_results=args.max_results,
                sort=args.sort,
                compact=args.compact,
                api_key=args.api_key,
                email=args.email,
            )

        if args.format == "markdown":
            output_content = format_as_markdown(result, compact=args.compact)
        else:
            indent = args.indent if args.indent > 0 else None
            output_content = json.dumps(result, indent=indent, ensure_ascii=False)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_content)
                f.write("\n")
            sys.stderr.write(f"[INFO] Results written to {args.output}\n")
        else:
            sys.stdout.write(output_content)
            sys.stdout.write("\n")

        sys.exit(0)

    except Exception as e:
        sys.stderr.write(f"[ERROR] {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

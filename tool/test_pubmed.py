#!/usr/bin/env python3
"""Unit tests for tool/pubmed_query.py parsing and formatting."""

import xml.etree.ElementTree as ET
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool import query_pubmed, search_pubmed, fetch_details
from tool.pubmed_query import (
    _parse_abstract,
    _parse_authors,
    _parse_pub_date,
    format_as_markdown,
)

SAMPLE_XML = """
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>34567890</PMID>
      <Article>
        <ArticleTitle>Mathematical modeling of renal microcirculation.</ArticleTitle>
        <Journal>
          <Title>American Journal of Physiology</Title>
          <ISOAbbreviation>Am J Physiol</ISOAbbreviation>
          <JournalIssue>
            <PubDate>
              <Year>2023</Year>
              <Month>May</Month>
            </PubDate>
          </JournalIssue>
        </Journal>
        <AuthorList>
          <Author>
            <LastName>Layton</LastName>
            <ForeName>Anita T</ForeName>
            <Initials>AT</Initials>
          </Author>
          <Author>
            <LastName>Edwards</LastName>
            <ForeName>Aurélie</ForeName>
            <Initials>A</Initials>
          </Author>
        </AuthorList>
        <Abstract>
          <AbstractText Label="BACKGROUND">Renal microcirculation plays a key role.</AbstractText>
          <AbstractText Label="METHODS">We formulated a multi-scale ODE model.</AbstractText>
          <AbstractText Label="RESULTS">The model predicted autoregulation accurately.</AbstractText>
          <AbstractText Label="CONCLUSIONS">Valuable tool for studying nephron hemodynamics.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">34567890</ArticleId>
        <ArticleId IdType="doi">10.1152/ajprenal.00123.2023</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_parsing():
    root = ET.fromstring(SAMPLE_XML)
    article = root.find(".//Article")
    assert article is not None

    abstract = _parse_abstract(article)
    assert "BACKGROUND: Renal microcirculation plays a key role." in abstract
    assert "CONCLUSIONS: Valuable tool for studying nephron hemodynamics." in abstract

    authors = _parse_authors(article)
    assert authors == ["Layton AT", "Edwards A"]

    dates = _parse_pub_date(article)
    assert dates["year"] == "2023"
    assert "2023 May" in dates["pub_date"]

    data = {
        "query": "test query",
        "total_hits": 1,
        "count_returned": 1,
        "articles": [
            {
                "pmid": "34567890",
                "title": "Mathematical modeling of renal microcirculation.",
                "authors": authors,
                "journal": "Am J Physiol",
                "year": dates["year"],
                "pub_date": dates["pub_date"],
                "doi": "10.1152/ajprenal.00123.2023",
                "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/34567890/",
                "abstract": abstract,
            }
        ],
    }

    md = format_as_markdown(data, compact=True)
    assert "34567890" in md
    assert "Layton AT" in md

    md_full = format_as_markdown(data, compact=False)
    assert "BACKGROUND:" in md_full

    print("All unit tests passed successfully!")


if __name__ == "__main__":
    test_parsing()

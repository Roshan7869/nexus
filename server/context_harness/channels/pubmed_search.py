"""PubMed channel — biomedical and life sciences research.

PubMed indexes 36M+ citations from MEDLINE, life science journals, and
online books. Covers medicine, nursing, dentistry, veterinary medicine,
health care, and preclinical sciences (biochemistry, cell biology,
genetics, microbiology, pharmacology).

Uses the NCBI E-utilities API (esearch + efetch). Free, no key required
(3 req/sec without key, 10 with NCBI_API_KEY env var).
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedChannel:
    """PubMed biomedical/life-science search via NCBI E-utilities."""

    def __init__(self, api_key: str = "") -> None:
        env_key = __import__("os").environ.get("NCBI_API_KEY", "")
        self._api_key = api_key or env_key
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=45.0)
        return self._client

    def _base_params(self) -> dict:
        params: dict[str, Any] = {"db": "pubmed", "retmode": "json"}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def search(
        self,
        query: str,
        max_results: int = 10,
        sort: str = "relevance",
        date_range: str = "",
    ) -> list[dict]:
        """Search PubMed for biomedical literature.

        Args:
            query: PubMed search query (supports MeSH terms, boolean ops,
                   field tags like [au], [tiab], [mh])
                   e.g. '"CRISPR"[mh] AND "cancer"[tiab]'
            max_results: number of results (max 200)
            sort: 'relevance' (default), 'pub_date', 'Author', 'JournalName'
            date_range: optional date filter, e.g. '2023/01/01:2026/12/31'

        Returns:
            list of normalized dicts with title, authors, abstract, year,
            journal, pmid, doi, pmc_id, mesh_terms, publication_types.
        """
        full_query = query
        if date_range:
            full_query = f"({query}) AND (\"{date_range}\"[dp])"

        # Step 1: esearch to get PMIDs
        params = self._base_params()
        params.update({
            "term": full_query,
            "retmax": min(max_results, 200),
            "sort": sort,
        })
        try:
            resp = await self.client.get(ESEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return []
        except Exception:
            return []

        # Step 2: efetch to get full records
        await asyncio.sleep(0.35 if not self._api_key else 0.12)  # rate limit
        fetch_params = {
            "db": "pubmed", "retmode": "xml", "id": ",".join(pmids),
        }
        if self._api_key:
            fetch_params["api_key"] = self._api_key
        try:
            resp2 = await self.client.get(EFETCH_URL, params=fetch_params)
            resp2.raise_for_status()
            return self._parse_pubmed_xml(resp2.text)
        except Exception:
            return []

    async def search_mesh(self, mesh_term: str, max_results: int = 10) -> list[dict]:
        """Search by MeSH (Medical Subject Heading) term.

        MeSH is the NLM controlled vocabulary. Example mesh terms:
        'Neoplasms', 'CRISPR-Cas Systems', 'Machine Learning', 'Drug Therapy'.
        """
        return await self.search(f'"{mesh_term}"[mh]', max_results=max_results)

    def _parse_pubmed_xml(self, xml_text: str) -> list[dict]:
        """Parse PubMed efetch XML into normalized dicts."""
        results: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return results

        for article in root.findall(".//PubmedArticle"):
            # Title
            title_el = article.find(".//ArticleTitle")
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title:
                continue

            # Abstract — may have multiple labeled sections
            abstract_parts: list[str] = ""
            for abs_text in article.findall(".//Abstract/AbstractText"):
                label = abs_text.get("Label", "")
                # Gather all text including child elements
                text = "".join(abs_text.itertext()).strip()
                if label:
                    abstract_parts += f"{label}: {text} "
                else:
                    abstract_parts += text + " "
            abstract = abstract_parts.strip()[:4000]

            # Authors
            authors: list[str] = []
            for author in article.findall(".//Author"):
                last = author.find("LastName")
                fore = author.find("ForeName")
                initials = author.find("Initials")
                name = ""
                if fore is not None and last is not None:
                    name = f"{fore.text} {last.text}"
                elif last is not None and initials is not None:
                    name = f"{last.text} {initials.text}"
                elif last is not None:
                    name = last.text or ""
                if name:
                    authors.append(name.strip())

            # Journal + year
            journal_el = article.find(".//Journal/Title")
            journal = journal_el.text if journal_el is not None else ""
            year = None
            year_el = (article.find(".//PubDate/Year")
                       or article.find(".//ArticleDate/Year"))
            if year_el is not None and year_el.text:
                try:
                    year = int(year_el.text)
                except ValueError:
                    year = None

            # IDs: PMID, DOI, PMC
            pmid = ""
            pmid_el = article.find(".//PMID")
            if pmid_el is not None:
                pmid = pmid_el.text or ""
            doi = ""
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
            pmc_id = ""
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "pmc":
                    pmc_id = aid.text or ""

            # MeSH terms
            mesh_terms: list[str] = []
            for mh in article.findall(".//MeshHeading/DescriptorName"):
                if mh.text:
                    mesh_terms.append(mh.text)

            # Publication types
            pub_types: list[str] = []
            for pt in article.findall(".//PublicationType"):
                if pt.text:
                    pub_types.append(pt.text)

            results.append({
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "year": year,
                "journal": journal,
                "pmid": pmid,
                "doi": doi,
                "pmc_id": pmc_id,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                "mesh_terms": mesh_terms,
                "publication_types": pub_types,
                "citation_count": 0,  # PubMed doesn't expose citation counts
            })
        return results


_channel: PubMedChannel | None = None


def get_channel() -> PubMedChannel:
    global _channel
    if _channel is None:
        _channel = PubMedChannel()
    return _channel

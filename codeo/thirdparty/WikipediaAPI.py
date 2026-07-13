# maintainer: starlight.ai
# author: starlight.ai
# version v0.0.2
# purpose: Wikipedia API wrapper — search and summary via REST API
# changelog:
#  v0.0.1 ==> initial: WikipediaSearchResult, WikipediaSummary, search + summary
#  v0.0.2 ==> fix: search endpoint URL migrated from /api/rest_v1/ to /w/rest.php/v1/;
#             adapt response fields (pageid→id, extract→excerpt); strip HTML from excerpt

# Design rationale:
# - Uses the public Wikimedia REST API. No auth, no API key.
# - Search migrated from /api/rest_v1/ to /w/rest.php/v1/ in 2026 (API Portal deprecation).
#   Summary and HTML endpoints remain at /api/rest_v1/ for now.
# - No third-party dependencies beyond requests + pydantic.
# - Wikipedia pages are just URLs — rag_html_to_blocks handles full page ingestion.
#   This module only covers search and quick summaries.
# - User-Agent required by Wikimedia policy. Set in the module-level _HEADERS.
#
# notes: requires pip install requests, pydantic

import re
import requests
from pydantic import BaseModel, Field

_HEADERS = {"User-Agent": "StarlightRAG/0.1 (research; davidbernat@starlight.ai)"}
_REST_API = "https://en.wikipedia.org/api/rest_v1"
_SEARCH_API = "https://en.wikipedia.org/w/rest.php/v1"


class WikipediaSearchResult(BaseModel):
    """A single result from Wikipedia search."""

    title: str = Field(description="Page title")
    pageid: int = Field(description="Wikipedia internal page ID")
    description: str | None = Field(default=None, description="Wikidata short description")
    extract: str | None = Field(default=None, description="First ~500 chars as plain text")
    thumbnail: str | None = Field(default=None, description="Thumbnail image URL")


class WikipediaSummary(BaseModel):
    """Page summary from Wikipedia REST API."""

    title: str = Field(description="Page title")
    pageid: int = Field(description="Wikipedia internal page ID")
    extract: str = Field(description="First ~500 chars plain text extract")
    extract_html: str | None = Field(default=None, description="First ~500 chars as HTML")
    description: str | None = Field(default=None, description="Wikidata short description")
    thumbnail: str | None = Field(default=None, description="Thumbnail image URL")
    url: str = Field(description="Canonical Wikipedia URL")


class WikipediaAPI:
    """Wikipedia search and summary. Stateless — config per call."""

    @staticmethod
    def search(q: str, limit: int = 10) -> list[WikipediaSearchResult]:
        """Search Wikipedia pages by query.

        Args:
            q: Search term.
            limit: Max results (default 10).

        Returns:
            List of WikipediaSearchResult.
        """
        resp = requests.get(
            f"{_SEARCH_API}/search/page",
            params={"q": q, "limit": limit},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # strip HTML tags from excerpt (the new endpoint returns <span class="searchmatch">)
        _strip = re.compile(r"<[^>]+>")

        results: list[WikipediaSearchResult] = []
        for page in data.get("pages", []):
            excerpt = page.get("excerpt")
            if excerpt:
                excerpt = _strip.sub("", excerpt)
            thumb = page.get("thumbnail")
            results.append(WikipediaSearchResult(
                title=page.get("title", ""),
                pageid=page.get("id", 0),
                description=page.get("description"),
                extract=excerpt,
                thumbnail=thumb.get("url") if thumb else None,
            ))
        return results

    @staticmethod
    def summary(title: str) -> WikipediaSummary:
        """Get page summary for a given title.

        Args:
            title: Wikipedia page title (case-sensitive, spaces allowed).

        Returns:
            WikipediaSummary with extract, thumbnail, and metadata.
        """
        resp = requests.get(
            f"{_REST_API}/page/summary/{title}",
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        thumb = data.get("thumbnail")
        return WikipediaSummary(
            title=data.get("title", ""),
            pageid=data.get("pageid", 0),
            extract=data.get("extract", ""),
            extract_html=data.get("extract_html"),
            description=data.get("description"),
            thumbnail=thumb.get("source") if thumb else None,
            url=f"https://en.wikipedia.org/wiki/{data.get('title', '').replace(' ', '_')}",
        )

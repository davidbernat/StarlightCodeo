# maintainer: starlight.ai
# author: starlight.ai
# version v0.0.1
# purpose: arXiv API wrapper — search papers by date/query, return Pydantic models
# changelog:
#  v0.0.1 ==> extracted from fetch_arxiv.py; class-based API

# Design rationale:
# - Separates the arXiv API concern from CLI handling. ArxivApi.search() can
#   be called from test_runner.py, QBoot operators, or interactively.
# - Pydantic models ensure structured output for QBoot's AS TYPEHINT coercion.
# - Thin wrapper over the `arxiv` PyPI package; no retry/pagination logic
#   beyond what the upstream client provides.
#
# notes: requires pip install arxiv, pydantic

import arxiv
from datetime import datetime
from pydantic import BaseModel


class AuthorField(BaseModel):
    name: str
    affiliation: list[str] | None = None


class LinkField(BaseModel):
    href: str
    title: str | None = None
    rel: str
    content_type: str | None = None


class ArxivPaper(BaseModel):
    entry_id: str
    short_id: str
    title: str
    authors: list[AuthorField]
    summary: str
    comment: str | None = None
    journal_ref: str | None = None
    doi: str | None = None
    published: datetime
    updated: datetime
    primary_category: str
    categories: list[str]
    pdf_url: str | None = None
    links: list[LinkField]


class ArxivApi:
    """arXiv paper search. Stateless — all config per call."""

    @staticmethod
    def search(
        since: str,
        until: str,
        query: str | None = None,
    ) -> list[ArxivPaper]:
        """
        Fetch arXiv papers matching a query and date range.

        Args:
            since: Start date in YYYYMMDDHHMM format.
            until: End date in YYYYMMDDHHMM format.
            query: Optional search terms (AND by default, supports
                au:/ti:/cat: prefixes and explicit OR).

        Returns:
            List of ArxivPaper instances sorted by submittedDate descending.
        """
        date_q = f"submittedDate:[{since} TO {until}]"
        full_q = f"({query}) AND {date_q}" if query else date_q

        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(
            query=full_q,
            max_results=10000,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        papers: list[ArxivPaper] = []
        for r in client.results(search):
            papers.append(ArxivPaper(
                entry_id=r.entry_id,
                short_id=r.get_short_id(),
                title=r.title,
                authors=[AuthorField(name=a.name, affiliation=a.affiliation)
                         for a in r.authors],
                summary=r.summary,
                comment=r.comment,
                journal_ref=r.journal_ref,
                doi=r.doi,
                published=r.published,
                updated=r.updated,
                primary_category=r.primary_category,
                categories=r.categories,
                pdf_url=r.pdf_url,
                links=[LinkField(href=l.href, title=l.title, rel=l.rel,
                                 content_type=l.content_type) for l in r.links],
            ))
        return papers

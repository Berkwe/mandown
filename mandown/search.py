"""
Parallel orchestration for Naver, MangaDex, and AniList search.

Use :func:`search_all` as an async generator. Each provider runs in a worker
thread so the existing synchronous HTTP clients can execute concurrently.
WEBTOON matches are taken from AniList ``externalLinks`` instead of running a
separate WEBTOON search. The WEBTOON search provider can be enabled as a
fallback when AniList contains no WEBTOON links. Results are yielded as
``(source, matches)`` tuples as soon as they become available:

.. code-block:: python

    async for source, matches in mandown.search_all("tower of god"):
        print(source, [match.title for match in matches])

The synchronous :func:`mandown.search` API remains available for callers that
need a single dictionary result or a source filter.
"""

import asyncio
from collections.abc import AsyncIterator, Callable

from . import search_anilist, search_mangadex, search_naver, search_webtoons
from .search_common import SearchItem, SearchResults
from .sources.base_source import SourceSearchResult

SearchProvider = Callable[[str], list[SourceSearchResult]]
SearchBatch = tuple[str, list[SearchItem]]

SEARCH_PROVIDERS: dict[str, SearchProvider] = {
    "naver": search_naver.search,
    "webtoons": search_webtoons.search,
    "mangadex": search_mangadex.search,
    "anilist": search_anilist.search,
}


def _webtoons_from_anilist(
    matches: list[SourceSearchResult],
) -> list[SourceSearchResult]:
    results: list[SourceSearchResult] = []
    seen_urls: set[str] = set()
    for match in matches:
        external_links = match.extra.get("externalLinks")
        if not isinstance(external_links, list):
            continue
        for link in external_links:
            if not isinstance(link, dict):
                continue
            site = link.get("site")
            url = link.get("url")
            if (
                not isinstance(site, str)
                or site.casefold() != "webtoon"
                or not isinstance(url, str)
                or not url
                or url in seen_urls
            ):
                continue
            seen_urls.add(url)
            results.append(
                SourceSearchResult(
                    title=match.title,
                    url=url,
                    authors=match.authors,
                    cover_art=match.cover_art,
                    extra=dict(match.extra),
                )
            )
    return results


async def search_all(
    query: str,
    *,
    webtoons_fallback: bool = False,
) -> AsyncIterator[SearchBatch]:
    """
    Search providers concurrently and yield each completed batch.

    WEBTOON results are derived from AniList external links. The standalone
    WEBTOON provider only runs when ``webtoons_fallback`` is true and AniList
    returned no WEBTOON links.

    :param query: Series title or search phrase.
    :param webtoons_fallback: Search WEBTOON directly when AniList contains no
        WEBTOON external links. Disabled by default.
    :yields: ``(source, list[SearchItem])`` in provider completion order, with
        the derived WEBTOON batch immediately following AniList.
    :raises ValueError: If ``query`` is empty or contains only whitespace.
    """
    normalized_query = query.strip() if query else ""
    if not normalized_query:
        raise ValueError("Search query cannot be empty.")

    async def run_provider(
        source: str,
        provider: SearchProvider,
    ) -> tuple[str, list[SourceSearchResult]]:
        matches = await asyncio.to_thread(provider, normalized_query)
        return source, matches

    has_anilist = "anilist" in SEARCH_PROVIDERS
    tasks = [
        asyncio.create_task(run_provider(source, provider))
        for source, provider in SEARCH_PROVIDERS.items()
        if source != "webtoons" or not has_anilist
    ]
    try:
        for completed in asyncio.as_completed(tasks):
            source, matches = await completed
            yield source, [SearchItem(source, match) for match in matches]
            if source == "anilist" and "webtoons" in SEARCH_PROVIDERS:
                webtoons_matches = _webtoons_from_anilist(matches)
                if not webtoons_matches and webtoons_fallback:
                    webtoons_matches = await asyncio.to_thread(
                        SEARCH_PROVIDERS["webtoons"],
                        normalized_query,
                    )
                yield "webtoons", [
                    SearchItem("webtoons", match) for match in webtoons_matches
                ]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "SEARCH_PROVIDERS",
    "SearchBatch",
    "SearchItem",
    "SearchResults",
    "search_all",
]

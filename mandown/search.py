"""
Parallel orchestration for Naver, MangaDex, and AniList search.

Use :func:`search_all` as an async generator. Each provider runs in a worker
thread so the existing synchronous HTTP clients can execute concurrently.
URLs returned by AniList that belong to Naver, WEBTOON, or MangaDex are also
added to that source's batch. The WEBTOON search provider can be enabled as a
fallback when AniList contains no WEBTOON links. By default, results are
yielded as ``(source, matches)`` tuples as soon as they can no longer be
enriched by AniList:

.. code-block:: python

    async for source, matches in mandown.search_all("tower of god"):
        print(source, [match.title for match in matches])

Pass ``deduplicate=True`` to merge matches by AniList, MyAnimeList, then Naver
identifier and receive one final ``("merged", matches)`` batch.

The synchronous :func:`mandown.search` API remains available for callers that
need a single dictionary result or a source filter.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from urllib.parse import urlparse

from . import search_anilist, search_mangadex, search_naver, search_webtoons
from .errors import SourceResponseError
from .search_common import SearchItem, SearchResults, parse_int_identifier
from .sources.base_source import SourceSearchResult

SearchProvider = Callable[[str], list[SourceSearchResult]]
SearchBatch = tuple[str, list[SearchItem]]

SEARCH_PROVIDERS: dict[str, SearchProvider] = {
    "naver": search_naver.search,
    "webtoons": search_webtoons.search,
    "mangadex": search_mangadex.search,
    "anilist": search_anilist.search,
}


ANILIST_URL_HOSTS: dict[str, tuple[str, ...]] = {
    "naver": ("comic.naver.com", "series.naver.com"),
    "webtoons": ("webtoons.com",),
    "mangadex": ("mangadex.org",),
}


def _source_for_url(url: str) -> str | None:
    hostname = (urlparse(url).hostname or "").casefold()
    for source, domains in ANILIST_URL_HOSTS.items():
        if any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in domains
        ):
            return source
    return None


def _source_matches_from_anilist(
    matches: list[SourceSearchResult],
) -> dict[str, list[SourceSearchResult]]:
    results = {source: [] for source in ANILIST_URL_HOSTS}
    seen_urls = {source: set() for source in ANILIST_URL_HOSTS}
    for match in matches:
        links: list[object] = [{"url": match.url}]
        external_links = match.extra.get("externalLinks")
        if isinstance(external_links, list):
            links.extend(external_links)
        for link in links:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url:
                continue
            source = _source_for_url(url)
            if source is None or url in seen_urls[source]:
                continue
            seen_urls[source].add(url)
            results[source].append(
                SourceSearchResult(
                    title=match.title,
                    url=url,
                    authors=match.authors,
                    cover_art=match.cover_art,
                    extra=dict(match.extra),
                    identifiers=dict(match.identifiers),
                )
            )
    return results


def _merge_matches(
    matches: list[SourceSearchResult],
    additions: list[SourceSearchResult],
) -> list[SourceSearchResult]:
    merged = list(matches)
    seen_urls = {match.url for match in matches}
    for match in additions:
        if match.url not in seen_urls:
            seen_urls.add(match.url)
            merged.append(match)
    return merged


def _merge_item(groups: list[SearchItem], item: SearchItem) -> SearchItem:
    for identifier in ("anilist_id", "mal_id", "naver_id"):
        value = parse_int_identifier(item.identifiers[identifier])
        if value is None:
            continue
        for group in groups:
            group_value = parse_int_identifier(group.identifiers[identifier])
            if group_value == value:
                group.merge(item)
                return group
    groups.append(item)
    return item


async def search_all(
    query: str,
    *,
    webtoons_fallback: bool = False,
    retries: int = 2,
    deduplicate: bool = False,
) -> AsyncIterator[SearchBatch]:
    """
    Search providers concurrently and yield each completed batch.

    AniList result and external-link URLs belonging to Naver, WEBTOON, or
    MangaDex are merged into the matching source batch. A completed Naver or
    MangaDex batch may therefore wait for AniList before it is yielded. The
    standalone WEBTOON provider only runs when ``webtoons_fallback`` is true
    and AniList returned no WEBTOON links. A provider that raises
    :class:`~mandown.errors.SourceResponseError` is called again up to
    ``retries`` times. If every attempt fails, the last error is raised.
    With ``deduplicate=True``, all completed batches are compared by AniList,
    MyAnimeList, then Naver identifier. Matching results are combined, while
    unmatched results remain as separate groups in the final ``"merged"``
    batch.

    :param query: Series title or search phrase.
    :param webtoons_fallback: Search WEBTOON directly when AniList contains no
        WEBTOON external links. Disabled by default.
    :param retries: Number of additional attempts after a source response
        error. Defaults to 2, for at most 3 provider calls.
    :param deduplicate: Merge matches that share a non-None AniList,
        MyAnimeList, or Naver identifier and retain unmatched items as separate
        groups. Disabled by default.
    :yields: ``(source, list[SearchItem])`` in provider completion order, with
        AniList-derived matches merged into supported source batches.
    :raises ValueError: If ``query`` is empty or ``retries`` is not a
        non-negative integer.
    :raises SourceResponseError: If a provider still fails after all retries.
    """
    normalized_query = query.strip() if query else ""
    if not normalized_query:
        raise ValueError("Search query cannot be empty.")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("Search retries must be a non-negative integer.")
    if not isinstance(deduplicate, bool):
        raise ValueError("Search deduplicate must be a boolean.")

    merged_groups: list[SearchItem] = []
    async for batch in _search_batches(
        normalized_query,
        webtoons_fallback=webtoons_fallback,
        retries=retries,
    ):
        if not deduplicate:
            yield batch
            continue
        for item in batch[1]:
            _merge_item(merged_groups, item)
    if deduplicate:
        yield "merged", merged_groups


async def _search_batches(
    normalized_query: str,
    *,
    webtoons_fallback: bool,
    retries: int,
) -> AsyncIterator[SearchBatch]:
    async def run_provider(
        source: str,
        provider: SearchProvider,
    ) -> tuple[str, list[SourceSearchResult]]:
        for attempt in range(retries + 1):
            try:
                matches = await asyncio.to_thread(provider, normalized_query)
                if matches is None:
                    return source, []
                if not isinstance(matches, list):
                    raise SourceResponseError(
                        f"{source} search provider returned "
                        f"{type(matches).__name__}, expected a list or None"
                    )
                return source, matches
            except SourceResponseError:
                if attempt == retries:
                    raise
        raise AssertionError("Search provider retry loop ended unexpectedly")

    has_anilist = "anilist" in SEARCH_PROVIDERS
    tasks = [
        asyncio.create_task(run_provider(source, provider))
        for source, provider in SEARCH_PROVIDERS.items()
        if source != "webtoons" or not has_anilist
    ]
    anilist_additions = {source: [] for source in ANILIST_URL_HOSTS}
    held_batches: list[tuple[str, list[SourceSearchResult]]] = []
    anilist_complete = not has_anilist

    def make_batch(
        source: str,
        matches: list[SourceSearchResult],
    ) -> SearchBatch:
        merged = _merge_matches(matches, anilist_additions.get(source, []))
        return source, [SearchItem(source, match) for match in merged]

    try:
        for completed in asyncio.as_completed(tasks):
            try:
                source, matches = await completed
            except SourceResponseError:
                for held_source, held_matches in held_batches:
                    yield make_batch(held_source, held_matches)
                held_batches.clear()
                raise

            if source == "anilist":
                anilist_additions = _source_matches_from_anilist(matches)
                anilist_complete = True
                for held_source, held_matches in held_batches:
                    yield make_batch(held_source, held_matches)
                held_batches.clear()

                yield source, [SearchItem(source, match) for match in matches]
                if "webtoons" not in SEARCH_PROVIDERS:
                    continue
                webtoons_matches = anilist_additions["webtoons"]
                if not webtoons_matches and webtoons_fallback:
                    _, webtoons_matches = await run_provider(
                        "webtoons",
                        SEARCH_PROVIDERS["webtoons"],
                    )
                yield "webtoons", [
                    SearchItem("webtoons", match) for match in webtoons_matches
                ]
            elif not anilist_complete and source in ANILIST_URL_HOSTS:
                held_batches.append((source, matches))
            else:
                yield make_batch(source, matches)
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

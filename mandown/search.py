"""Deprecated compatibility adapters for the AniList-only search flow.

New async code should use :class:`mandown.AniListClient`. ``search`` preserves
the historical dictionary/list result shape for synchronous callers, while
``search_all`` preserves the old async-generator shape with one AniList batch.
"""

import asyncio
import warnings
from collections.abc import AsyncIterator

from .anilist import AniListClient, AniListManga, AniListMangaSummary
from .search_common import SearchItem, SearchResults
from .sources.base_source import SourceSearchResult

SearchBatch = tuple[str, list[SearchItem]]
_RESULT_KEYS = ("naver", "webtoons", "mangadex", "anilist")


def search(title: str, source: str | None = None) -> SearchResults:
    """Deprecated synchronous AniList adapter."""

    warnings.warn(
        "mandown.search() is deprecated; use AniListClient.search_manga().",
        DeprecationWarning,
        stacklevel=2,
    )
    normalized = title.strip() if isinstance(title, str) else ""
    if not normalized:
        raise ValueError("Search title cannot be empty.")
    source_key = source.strip().lower() if isinstance(source, str) else source
    if source_key not in (None, "anilist"):
        raise ValueError(
            f"Search source {source!r} is no longer active. "
            "Use AniList search and resolve one of its supported external links."
        )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "mandown.search() cannot run inside an active event loop; "
            "use await AniListClient().search_manga(...)."
        )

    matches = asyncio.run(_search_items(normalized))
    output = SearchResults()
    for key in _RESULT_KEYS:
        output[key] = (matches or None) if key == "anilist" else None
    return output


async def search_all(
    query: str,
    *,
    webtoons_fallback: bool = False,
    retries: int = 2,
    deduplicate: bool = False,
) -> AsyncIterator[SearchBatch]:
    """Deprecated async-generator adapter yielding one AniList batch."""

    warnings.warn(
        "mandown.search_all() is deprecated; use AniListClient.search_manga().",
        DeprecationWarning,
        stacklevel=2,
    )
    normalized = query.strip() if isinstance(query, str) else ""
    if not normalized:
        raise ValueError("Search query cannot be empty.")
    if webtoons_fallback or deduplicate or retries != 2:
        raise ValueError(
            "webtoons_fallback, deduplicate, and custom retries belong to the "
            "retired multi-provider search. Use AniListClient options instead."
        )
    yield "anilist", await _search_items(normalized)


async def _search_items(query: str) -> list[SearchItem]:
    async with AniListClient() as client:
        response = await client.search_manga(query, include_external_links=True)
        return [_to_search_item(client, item) for item in response.items]


def _to_search_item(
    client: AniListClient,
    manga: AniListMangaSummary | AniListManga,
) -> SearchItem:
    external_links = manga.external_links if isinstance(manga, AniListManga) else ()
    supported = client.extract_supported_sources(external_links)
    result_url = (
        supported[0].url
        if supported
        else manga.site_url or f"https://anilist.co/manga/{manga.id}"
    )
    title = manga.title.english or manga.title.romaji or manga.title.native or str(manga.id)
    cover = manga.cover_image
    external_payload = [
        {
            "site": link.site,
            "url": link.url,
            "type": link.type,
            "language": link.language,
            "isDisabled": link.is_disabled,
            "notes": link.notes,
        }
        for link in external_links
    ]
    return SearchItem(
        "anilist",
        SourceSearchResult(
            title=title,
            url=result_url,
            cover_art=(
                (cover.extra_large or cover.large or cover.medium or "")
                if cover is not None
                else ""
            ),
            extra={
                "anilist_id": manga.id,
                "mal_id": manga.id_mal,
                "title": {
                    "romaji": manga.title.romaji,
                    "english": manga.title.english,
                    "native": manga.title.native,
                },
                "externalLinks": external_payload,
                "site_url": manga.site_url,
            },
            identifiers={"anilist_id": manga.id, "mal_id": manga.id_mal},
        ),
    )


__all__ = ["SearchBatch", "SearchItem", "SearchResults", "search", "search_all"]

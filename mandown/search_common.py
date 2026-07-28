"""Shared result objects used by search providers and orchestrators."""

from functools import cached_property
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from .comic import BaseComic
from .sources.base_source import SourceSearchResult

IDENTIFIER_KEYS = (
    "anilist_id",
    "mal_id",
    "naver_id",
    "webtoons_id",
    "mangadex_id",
)
URL_SOURCE_KEYS = ("naver", "webtoons", "mangadex", "anilist")


def empty_identifiers() -> dict[str, int | str | None]:
    return dict.fromkeys(IDENTIFIER_KEYS)


def parse_int_identifier(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            return None
        return parsed if parsed > 0 else None
    except Exception:  # External identifiers must never break a search.
        return None


def parse_string_identifier(value: Any) -> str | None:
    try:
        parsed = str(value).strip() if value is not None else ""
        return parsed or None
    except Exception:  # External identifiers must never break a search.
        return None


def parse_naver_id(url: Any) -> str | None:
    try:
        query = parse_qs(urlparse(str(url)).query)
        for key in ("titleId", "productNo"):
            value = query.get(key, [None])[0]
            if value is not None and str(value).strip():
                return str(value).strip()
    except Exception:  # External URLs must never break a search.
        return None
    return None


def parse_webtoons_id(url: Any) -> str | None:
    try:
        value = parse_qs(urlparse(str(url)).query).get("title_no", [None])[0]
        return str(value).strip() if value is not None and str(value).strip() else None
    except Exception:  # External URLs must never break a search.
        return None


def parse_mangadex_id(value: Any) -> str | None:
    try:
        parsed = urlparse(str(value))
        candidate = parsed.path.rstrip("/").split("/")[-1] if parsed.scheme else str(value)
        return str(UUID(candidate))
    except Exception:  # External identifiers must never break a search.
        return None


def source_for_url(url: Any) -> str | None:
    try:
        parsed = urlparse(str(url))
        hostname = (parsed.hostname or "").casefold()
        if hostname == "anilist.co" or hostname.endswith(".anilist.co"):
            return "anilist"
        if hostname == "mangadex.org" or hostname.endswith(".mangadex.org"):
            return "mangadex"
        if hostname == "webtoons.com" or hostname.endswith(".webtoons.com"):
            return "webtoons"
        if hostname in {"comic.naver.com", "series.naver.com"} or hostname.endswith(
            (".comic.naver.com", ".series.naver.com")
        ):
            return "naver"
    except Exception:  # External URLs must never break a search.
        return None
    return None


class SearchItem:
    """
    One lightweight series match.

    ``identifiers`` always contains AniList, MyAnimeList, Naver, WEBTOON, and
    MangaDex keys. ``urls`` contains one slot per source, while ``titles`` and
    ``sources`` retain merged provenance. The first access to ``comic`` calls
    :func:`mandown.query`; later accesses return the cached :class:`BaseComic`.
    """

    def __init__(self, source: str, result: SourceSearchResult):
        self.source = source
        self.title = result.title
        self.url = result.url
        self.authors = list(result.authors)
        self.cover_art = result.cover_art
        self.extra = dict(result.extra)
        self.identifiers = empty_identifiers()
        for key in IDENTIFIER_KEYS:
            value = result.identifiers.get(key)
            if value is None:
                value = self.extra.get(key)
            if key in {"anilist_id", "mal_id"}:
                value = parse_int_identifier(value)
            elif key == "mangadex_id":
                value = parse_mangadex_id(value)
            else:
                value = parse_string_identifier(value)
            self.identifiers[key] = value
        self._fill_identifier_fallbacks(result.url)

        self.urls: dict[str, str | None] = dict.fromkeys(URL_SOURCE_KEYS)
        result_url_source = source_for_url(result.url)
        if result_url_source is not None:
            self.urls[result_url_source] = result.url
        elif source in self.urls:
            self.urls[source] = result.url
        self._add_external_urls()
        anilist_id = self.identifiers["anilist_id"]
        if source == "anilist" and isinstance(anilist_id, int):
            self.urls["anilist"] = f"https://anilist.co/manga/{anilist_id}"

        self.titles = self._result_titles()
        self.sources = [source or "unknown"]

    @cached_property
    def comic(self) -> BaseComic:
        from .api import query  # local import avoids api/search import cycle

        if source_for_url(self.url) == "anilist":
            raise ValueError(
                "This AniList result has no Mandown-supported external source URL."
            )
        return query(self.url)

    def asdict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "authors": self.authors,
            "cover_art": self.cover_art,
            "extra": self.extra,
            "identifiers": self.identifiers,
            "urls": self.urls,
            "titles": self.titles,
            "sources": self.sources,
        }

    def __repr__(self) -> str:
        return f"SearchItem(source={self.source!r}, title={self.title!r}, url={self.url!r})"

    def merge(self, other: "SearchItem") -> None:
        for key in IDENTIFIER_KEYS:
            if self.identifiers[key] is None and other.identifiers[key] is not None:
                self.identifiers[key] = other.identifiers[key]
        for source, url in other.urls.items():
            if url is not None:
                self.urls[source] = url
        self.titles = list(dict.fromkeys([*self.titles, *other.titles]))
        self.sources = list(dict.fromkeys([*self.sources, *other.sources]))
        self.authors = list(dict.fromkeys([*self.authors, *other.authors]))
        if not self.cover_art and other.cover_art:
            self.cover_art = other.cover_art

    def _fill_identifier_fallbacks(self, url: str) -> None:
        fallback_parsers = {
            "naver_id": parse_naver_id,
            "webtoons_id": parse_webtoons_id,
            "mangadex_id": parse_mangadex_id,
        }
        for key, parser in fallback_parsers.items():
            if self.identifiers[key] is None:
                self.identifiers[key] = parser(url)

    def _add_external_urls(self) -> None:
        external_links = self.extra.get("externalLinks")
        if not isinstance(external_links, list):
            return
        for link in external_links:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url:
                continue
            source = source_for_url(url)
            if source is not None:
                self.urls[source] = url

    def _result_titles(self) -> list[str]:
        titles = [self.title] if self.title else []
        extra_titles = self.extra.get("title")
        if isinstance(extra_titles, dict):
            titles.extend(
                title
                for title in extra_titles.values()
                if isinstance(title, str) and title
            )
        return list(dict.fromkeys(titles)) or [self.url]


class SearchResults(dict[str, list[SearchItem] | None]):
    """
    Search matches keyed by ``naver``, ``webtoons``, ``mangadex``, and ``anilist``.

    A key maps to ``None`` when its catalog was skipped or returned no matches.
    ``asdict()`` converts every lightweight match to plain Python dictionaries.
    """

    def asdict(self) -> dict[str, list[dict] | None]:
        return {
            source: None if matches is None else [match.asdict() for match in matches]
            for source, matches in self.items()
        }

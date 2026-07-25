"""Cross-source series search."""

from functools import cached_property

from .comic import BaseComic
from .sources.base_source import SourceSearchResult


class SearchItem:
    """
    One lightweight series match.

    ``title``, ``url``, ``authors``, and ``cover_art`` are available without
    downloading the complete series. The first access to ``comic`` calls
    :func:`mandown.query`; later accesses return the cached :class:`BaseComic`.
    Use :meth:`asdict` when JSON-compatible search metadata is needed.
    """

    def __init__(self, source: str, result: SourceSearchResult):
        self.source = source
        self.title = result.title
        self.url = result.url
        self.authors = list(result.authors)
        self.cover_art = result.cover_art
        self.extra = dict(result.extra)

    @cached_property
    def comic(self) -> BaseComic:
        from .api import query  # local import avoids api/search import cycle

        return query(self.url)

    def asdict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "authors": self.authors,
            "cover_art": self.cover_art,
            "extra": self.extra,
        }

    def __repr__(self) -> str:
        return f"SearchItem(source={self.source!r}, title={self.title!r}, url={self.url!r})"


class SearchResults(dict[str, list[SearchItem] | None]):
    """
    Search matches keyed by ``naver``, ``webtoons``, and ``mangadex``.

    A key maps to ``None`` when its catalog returned no matches. ``asdict()``
    converts every lightweight match to plain Python dictionaries.
    """

    def asdict(self) -> dict[str, list[dict] | None]:
        return {
            source: None if matches is None else [match.asdict() for match in matches]
            for source, matches in self.items()
        }

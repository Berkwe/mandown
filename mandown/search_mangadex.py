"""MangaDex title search provider."""

import requests

from .sources.base_source import SourceSearchResult
from .sources.source_mangadex import MangaDexSource


def search(query: str) -> list[SourceSearchResult]:
    """Search MangaDex and return lightweight series results."""
    response = MangaDexSource._get(
        "https://api.mangadex.org/manga"
        f"?title={requests.utils.quote(query)}"
        "&limit=20&includes[]=author&includes[]=artist&includes[]=cover_art"
        "&order[relevance]=desc"
    )
    results: list[SourceSearchResult] = []
    for manga in response.json().get("data", []):
        manga_id = manga["id"]
        attributes = manga.get("attributes", {})
        titles = attributes.get("title") or {}
        display_title = titles.get("en") or next(iter(titles.values()), manga_id)
        authors: list[str] = []
        cover_art = ""
        for relationship in manga.get("relationships", []):
            relation_type = relationship.get("type")
            relation_attributes = relationship.get("attributes") or {}
            if relation_type in {"author", "artist"} and relation_attributes.get("name"):
                if relation_attributes["name"] not in authors:
                    authors.append(relation_attributes["name"])
            elif relation_type == "cover_art" and relation_attributes.get("fileName"):
                cover_art = (
                    f"https://uploads.mangadex.org/covers/{manga_id}/"
                    f"{relation_attributes['fileName']}"
                )
        results.append(
            SourceSearchResult(
                title=display_title,
                url=f"https://mangadex.org/title/{manga_id}",
                authors=tuple(authors),
                cover_art=cover_art,
            )
        )
    return results

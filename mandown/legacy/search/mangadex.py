"""Legacy provider text search. Not used by the active AniList search flow."""

import requests

from ...errors import SourceResponseError
from ...search_common import parse_int_identifier, parse_mangadex_id, parse_naver_id
from ...sources.base_source import SourceSearchResult
from ...sources.source_mangadex import MangaDexSource


def search(query: str) -> list[SourceSearchResult]:
    """Search MangaDex and return lightweight series results."""
    api_url = (
        "https://api.mangadex.org/manga"
        f"?title={requests.utils.quote(query)}"
        "&limit=20&includes[]=author&includes[]=artist&includes[]=cover_art"
        "&order[relevance]=desc"
    )
    try:
        response = MangaDexSource._get(api_url)
    except (requests.RequestException, RuntimeError) as error:
        raise SourceResponseError(
            f"MangaDex response error: url={api_url}, request failed: {error}"
        ) from error
    if response is None:
        raise SourceResponseError(
            f"MangaDex response error: url={api_url}, request returned no response"
        )
    payload = MangaDexSource._json_object(response, api_url)
    results: list[SourceSearchResult] = []
    for manga in MangaDexSource._sequence(payload.get("data")):
        if not isinstance(manga, dict):
            continue
        manga_id = manga.get("id")
        if not isinstance(manga_id, str) or not manga_id:
            continue
        attributes = MangaDexSource._mapping(manga.get("attributes"))
        titles = MangaDexSource._mapping(attributes.get("title"))
        links = MangaDexSource._mapping(attributes.get("links"))
        display_title = titles.get("en") or next(iter(titles.values()), manga_id)
        if not isinstance(display_title, str) or not display_title:
            display_title = manga_id
        authors: list[str] = []
        cover_art = ""
        for relationship in MangaDexSource._sequence(manga.get("relationships")):
            if not isinstance(relationship, dict):
                continue
            relation_type = relationship.get("type")
            relation_attributes = MangaDexSource._mapping(relationship.get("attributes"))
            if relation_type in {"author", "artist"} and relation_attributes.get("name"):
                name = relation_attributes["name"]
                if isinstance(name, str) and name not in authors:
                    authors.append(name)
            elif relation_type == "cover_art":
                file_name = relation_attributes.get("fileName")
                if not isinstance(file_name, str) or not file_name:
                    continue
                cover_art = (
                    f"https://uploads.mangadex.org/covers/{manga_id}/"
                    f"{file_name}"
                )
        results.append(
            SourceSearchResult(
                title=display_title,
                url=f"https://mangadex.org/title/{manga_id}",
                authors=tuple(authors),
                cover_art=cover_art,
                identifiers={
                    "anilist_id": parse_int_identifier(links.get("al")),
                    "mal_id": parse_int_identifier(links.get("mal")),
                    "naver_id": parse_naver_id(links.get("raw")),
                    "webtoons_id": None,
                    "mangadex_id": parse_mangadex_id(manga_id),
                },
            )
        )
    return results

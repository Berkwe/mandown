"""WEBTOON title search provider."""

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .request_utils import USER_AGENT
from .sources.base_source import SourceSearchResult

SEARCH_URL = "https://www.webtoons.com/en/search"


def search(query: str) -> list[SourceSearchResult]:
    """Search WEBTOON and return lightweight series results."""
    response = requests.get(
        SEARCH_URL,
        params={"keyword": query},
        headers={
            "Referer": "https://webtoons.com/",
            "User-Agent": USER_AGENT,
        },
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    results: list[SourceSearchResult] = []
    seen: set[str] = set()
    for card in soup.select("a._card_item[href*='title_no=']"):
        url = urljoin(response.url, str(card.get("href", "")))
        if not url or url in seen:
            continue
        seen.add(url)

        title_element = card.select_one(".title")
        if not title_element:
            continue
        author_element = card.select_one(".author")
        image = card.select_one("img")
        authors = (
            tuple(part.strip() for part in author_element.get_text().split("/") if part.strip())
            if author_element
            else ()
        )
        results.append(
            SourceSearchResult(
                title=title_element.get_text(strip=True),
                url=url,
                authors=authors,
                cover_art=str(image.get("src", "")) if image else "",
            )
        )
    return results

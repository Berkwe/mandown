"""Source adapter for Naver Series comics with a Naver Webtoon mirror."""

import re
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

from ..base import BaseChapter, BaseMetadata
from ..errors import SourceResponseError
from ..request_utils import USER_AGENT
from .common_source import CommonSource
from .source_naver import NaverWebtoonSource


class NaverSeriesSource(CommonSource):
    """
    Read a Naver Series comic through its downloadable Naver Webtoon mirror.

    Naver Series pages expose metadata and volume listings publicly, but their
    viewer requires a Naver account, a granted license, and the Series app.
    When the same comic is also published on Naver Webtoon, this adapter
    resolves that mirror and delegates chapter/image access to the existing
    Naver Webtoon adapter.
    """

    name = "Naver Series"
    domains = ["https://series.naver.com", "https://m.series.naver.com"]
    headers = {
        "Referer": "https://series.naver.com/",
        "User-Agent": USER_AGENT,
    }

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.product_no = self._product_no_from_url(url)
        self._detail_soup: BeautifulSoup | None = None
        self._webtoon_source: NaverWebtoonSource | None = None

    def _fetch_metadata(self) -> BaseMetadata:
        soup = self._get_detail_soup()
        title = self._required_text(soup.select_one(".end_head h2"), "title")
        authors = self._authors_from_soup(soup)
        genres = [
            link.get_text(" ", strip=True)
            for link in soup.select(".end_info a[href*='categoryTypeCode=genre']")
            if link.get_text(" ", strip=True)
        ]

        synopsis_nodes = soup.select("._synopsis")
        synopsis_node = synopsis_nodes[-1] if synopsis_nodes else None
        description = (
            synopsis_node.get_text("\n", strip=True).removesuffix("접기").strip()
            if synopsis_node
            else ""
        )
        cover_meta = soup.select_one("meta[property='og:image']")
        cover_art = cover_meta.get("content", "") if cover_meta else ""

        # Resolve here as well as in the chapter methods. A successful query
        # must mean that the result is actually downloadable by Mandown.
        self._get_webtoon_source(title=title, authors=authors)

        return BaseMetadata(
            title,
            authors,
            self._title_url(),
            genres,
            description,
            str(cover_art),
        )

    def _fetch_chapter_list(self) -> list[BaseChapter]:
        return self._get_webtoon_source().fetch_chapter_list()

    def _fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        return self._get_webtoon_source().fetch_chapter_image_list(chapter)

    def _get_detail_soup(self) -> BeautifulSoup:
        if self._detail_soup is not None:
            return self._detail_soup

        response = requests.get(self._title_url(), headers=self.headers, timeout=20)
        response.raise_for_status()
        self._detail_soup = BeautifulSoup(response.text, "lxml")
        return self._detail_soup

    def _get_webtoon_source(
        self,
        *,
        title: str | None = None,
        authors: list[str] | None = None,
    ) -> NaverWebtoonSource:
        if self._webtoon_source is not None:
            return self._webtoon_source

        soup = self._get_detail_soup()
        series_title = title or self._required_text(soup.select_one(".end_head h2"), "title")
        series_authors = authors if authors is not None else self._authors_from_soup(soup)
        total_count = self._total_count_from_soup(soup)
        query = self._normalized_title(series_title)

        response = requests.get(
            "https://comic.naver.com/api/search/all",
            params={"keyword": query},
            headers=self.headers,
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json().get("searchWebtoonResult", {}).get("searchViewList", [])

        candidates: list[tuple[int, dict]] = []
        expected_authors = {self._normalized_person(author) for author in series_authors}
        for row in rows:
            if not isinstance(row, dict) or not row.get("titleId"):
                continue
            if self._normalized_title(str(row.get("titleName") or "")) != query:
                continue

            candidate_count = row.get("articleTotalCount")
            if (
                total_count is not None
                and isinstance(candidate_count, int)
                and candidate_count != total_count
            ):
                continue

            candidate_authors = {
                self._normalized_person(str(artist.get("name") or ""))
                for artist in row.get("communityArtists") or []
                if isinstance(artist, dict)
            }
            author_matches = len(expected_authors & candidate_authors)
            candidates.append((author_matches, row))

        if not candidates:
            raise self._mirror_error(series_title)

        best_score = max(score for score, _ in candidates)
        best_rows = [row for score, row in candidates if score == best_score]
        if len(best_rows) != 1 or (expected_authors and best_score == 0):
            raise self._mirror_error(series_title)

        title_id = best_rows[0]["titleId"]
        mirror_url = "https://m.comic.naver.com/webtoon/list?" + urlencode(
            {"titleId": title_id, "sortOrder": "ASC"}
        )
        self._webtoon_source = NaverWebtoonSource(mirror_url)
        return self._webtoon_source

    def _mirror_error(self, title: str) -> SourceResponseError:
        return SourceResponseError(
            "Naver Series response error: "
            f"no downloadable Naver Webtoon mirror matched {title!r}. "
            "The native Naver Series viewer requires an authenticated license "
            "and is not supported by Mandown."
        )

    @staticmethod
    def _authors_from_soup(soup: BeautifulSoup) -> list[str]:
        authors: list[str] = []
        for row in soup.select(".end_info li"):
            role = row.select_one("span")
            author = row.select_one("a[href*='/search/search.series']")
            if role and role.get_text(" ", strip=True) in {"글", "그림", "원작"} and author:
                name = author.get_text(" ", strip=True)
                if name and name not in authors:
                    authors.append(name)
        return authors

    @staticmethod
    def _total_count_from_soup(soup: BeautifulSoup) -> int | None:
        node = soup.select_one(".end_total_episode strong")
        if not node:
            return None
        match = re.search(r"\d+", node.get_text("", strip=True).replace(",", ""))
        return int(match.group()) if match else None

    @staticmethod
    def _required_text(node, field: str) -> str:
        value = node.get_text(" ", strip=True) if node else ""
        if not value:
            raise SourceResponseError(
                f"Naver Series response error: missing {field} in detail page."
            )
        return value

    @staticmethod
    def _normalized_title(title: str) -> str:
        without_badges = re.sub(r"\s*[\[【][^\]】]+[\]】]\s*", " ", title)
        return re.sub(r"[\W_]+", "", without_badges, flags=re.UNICODE).casefold()

    @staticmethod
    def _normalized_person(name: str) -> str:
        return re.sub(r"[\W_]+", "", name, flags=re.UNICODE).casefold()

    def _title_url(self) -> str:
        return "https://series.naver.com/comic/detail.series?" + urlencode(
            {"productNo": self.product_no}
        )

    @staticmethod
    def _product_no_from_url(url: str) -> str:
        product_no = parse_qs(urlparse(url).query).get("productNo", [""])[0]
        if product_no.isdigit():
            return product_no
        raise ValueError("Naver Series URL must include a numeric productNo.")

    @staticmethod
    def check_url(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").casefold() in {"series.naver.com", "m.series.naver.com"}
            and parsed.path in {"/comic/detail.nhn", "/comic/detail.series"}
            and parse_qs(parsed.query).get("productNo", [""])[0].isdigit()
        )


def get_class() -> type[CommonSource]:
    return NaverSeriesSource

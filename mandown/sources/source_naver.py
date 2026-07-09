"""
Source file for comic.naver.com
"""
# pylint: disable=invalid-name

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..base import BaseChapter, BaseMetadata
from ..request_utils import USER_AGENT
from .common_source import CommonSource


class NaverWebtoonSource(CommonSource):
    name = "Naver Webtoon"
    domains = ["https://comic.naver.com", "https://m.comic.naver.com"]
    headers = {
        "Referer": "https://m.comic.naver.com/",
        "User-Agent": USER_AGENT,
    }

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.title_id = self._title_id_from_url(url)
        self._info: dict | None = None

    def _fetch_metadata(self) -> BaseMetadata:
        info = self._get_info()

        title = info["titleName"]
        authors = [artist["name"] for artist in info.get("communityArtists", [])]
        genres = [tag["tagName"] for tag in info.get("curationTagList", [])]
        description = (info.get("synopsis") or "").strip()
        cover_art = info.get("thumbnailUrl") or info.get("sharedThumbnailUrl") or ""

        return BaseMetadata(
            title,
            authors,
            self._title_url(),
            genres,
            description,
            cover_art,
        )

    def _fetch_chapter_list(self) -> list[BaseChapter]:
        chapters: list[BaseChapter] = []
        seen_chapter_numbers: set[str] = set()
        page = 1
        while True:
            data = self._get_json(
                f"https://comic.naver.com/api/article/list?titleId={self.title_id}&page={page}"
            )
            articles = data.get("articleList", [])
            if not articles:
                break

            added_count = 0
            for article in articles:
                chapter_number = str(article["no"])
                if chapter_number in seen_chapter_numbers:
                    continue
                seen_chapter_numbers.add(chapter_number)
                added_count += 1

                title = article.get("subtitle") or f"Episode {chapter_number}"
                chapters.append(
                    BaseChapter(
                        title,
                        self._chapter_url(chapter_number),
                        chapter_number=chapter_number,
                    )
                )

            if added_count == 0:
                break
            page += 1

        chapters.reverse()
        return chapters

    def _fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        soup = BeautifulSoup(
            requests.get(chapter.url, headers=self.headers, timeout=20).text,
            "lxml",
        )

        images: list[str] = []
        for image in soup.select(".toon_view_lst img.toon_image"):
            src = image.get("data-src") or image.get("src")
            if src and "bg_transparency" not in src:
                images.append(urljoin(chapter.url, src))
        return images

    def _get_info(self) -> dict:
        if self._info:
            return self._info

        self._info = self._get_json(
            f"https://comic.naver.com/api/article/list/info?titleId={self.title_id}"
        )
        return self._info

    def _get_json(self, url: str) -> dict:
        return requests.get(url, headers=self.headers, timeout=20).json()

    def _title_url(self) -> str:
        return f"https://m.comic.naver.com/webtoon/list?titleId={self.title_id}&sortOrder=ASC"

    def _chapter_url(self, chapter_number: str) -> str:
        query = urlencode(
            {
                "titleId": self.title_id,
                "no": chapter_number,
                "listSortOrder": "ASC",
            }
        )
        return f"https://m.comic.naver.com/webtoon/detail?{query}"

    @staticmethod
    def _title_id_from_url(url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        title_id = query.get("titleId", [""])[0]
        if title_id:
            return title_id

        match = re.search(r"/webtoon/(?:list|detail).*titleId=([0-9]+)", url)
        if match:
            return match.group(1)
        raise ValueError("Naver Webtoon URL must include titleId.")

    @staticmethod
    def check_url(url: str) -> bool:
        return bool(
            re.match(
                r"https://(m\.)?comic\.naver\.com/webtoon/(list|detail)\?.*titleId=",
                url,
            )
        )


def get_class() -> type[CommonSource]:
    return NaverWebtoonSource

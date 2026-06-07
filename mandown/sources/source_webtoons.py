"""
Source file for webtoons.com
"""
# pylint: disable=invalid-name

import re

import requests
from bs4 import BeautifulSoup

from ..base import BaseChapter, BaseMetadata
from .common_source import CommonSource


class WebtoonsSource(CommonSource):
    name = "Webtoons"
    domains = ["https://webtoons.com"]
    headers = {"Referer": "https://webtoons.com/"}

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._soup: BeautifulSoup | None = None

        title_no_index = url.index("?title_no=") + len("?title_no=")
        title_no_end_index: int | None = url.find("&", title_no_index)
        if title_no_end_index == -1:  # if '&' not found in url
            title_no_end_index = None

        self._title_no = int(url[title_no_index:title_no_end_index])

        self._title_path = "/".join(url.split("/")[3:6])

    def fetch_metadata(self) -> BaseMetadata:
        page = self._get_desktop_soup()
        title = page.select_one('meta[property="og:title"]')["content"]
        authors: list[str] = [
            s.strip()
            for s in page.select_one('meta[property="com-linewebtoon:webtoon:author"]')[
                "content"
            ].split("/")
        ]
        description: str = page.select_one("#content .summary").text
        cover_art_el = page.select_one("#content .detail_body.banner")

        meta_image = page.select_one('meta[property="og:image"]')
        
        if meta_image and "content" in meta_image.attrs:
            cover_art = meta_image["content"]
        else:
            # Yedek (Fallback) yöntem
            cover_art_el = page.select_one("#content .detail_header.challenge img")
            cover_art = cover_art_el["src"] if cover_art_el else ""

        genres_els = list(page.select(".info .genre"))

        for el in genres_els:
            span = el.find_all("span")
            for s in span:
                s.replace_with("")

        genres = [el.text for el in page.select(".info .genre")]

        return BaseMetadata(
            title,
            authors,
            f"https://www.webtoons.com/{self._title_path}/list?title_no={self._title_no}",
            genres,
            description,
            cover_art,
        )

    def fetch_chapter_list(self) -> list[BaseChapter]:
        data = self._get_chapters()
        print(self.name)
        BaseUrl = "https://www.webtoons.com"

        titles = [chapter["episodeTitle"] for chapter in data["result"]["episodeList"]]
        links = [BaseUrl+chapter["viewerLink"] for chapter in data["result"]["episodeList"]]

        chapters = [BaseChapter(t, u) for t, u in zip(titles, links)]
        return chapters

    def fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        soup = BeautifulSoup(requests.get(chapter.url).text, "lxml")
        images: list[str] = []
        for c in soup.select("div#_imageList > img"):
            images.append(c["data-url"])
        return images

    def _get_chapters(self) -> BeautifulSoup:

        Mobile_Api = f"https://m.webtoons.com/api/v1/webtoon/{self._title_no}/episodes?pageSize=99999&cursor=0"

        response = requests.get(Mobile_Api)

        return response.json()

    def _get_desktop_soup(self) -> BeautifulSoup:
        desktop_url = f"https://www.webtoons.com/{self._title_path}/list?title_no={self._title_no}"
        return BeautifulSoup(requests.get(desktop_url, headers=self.headers).text, "lxml")

    @staticmethod
    def check_url(url: str) -> bool:
        return bool(
            re.match(r"https://www.webtoons.com/.*/list\?title_no=.*", url)
            or re.match(r"https://m.webtoons.com/.*/list\?title_no=.*", url)
            or re.match(r"https://www.webtoons.com/.*/viewer\?title_no=.*", url)
        )


def get_class() -> type[CommonSource]:
    return WebtoonsSource

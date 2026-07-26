"""
Source file for webtoons.com
"""
# pylint: disable=invalid-name

import re
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..base import BaseChapter, BaseMetadata
from ..errors import SourceResponseError
from ..request_utils import USER_AGENT
from .base_source import SourceSearchResult
from .common_source import CommonSource


class WebtoonsSource(CommonSource):
    name = "Webtoons"
    domains = ["https://webtoons.com"]
    headers = {"Referer": "https://webtoons.com/"}
    request_timeout = 20
    max_request_attempts = 3
    retry_backoff_seconds = 0.25

    @classmethod
    def search(cls, title: str) -> list[SourceSearchResult]:
        response = requests.get(
            "https://www.webtoons.com/en/search",
            params={"keyword": title},
            headers={**cls.headers, "User-Agent": USER_AGENT},
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
                tuple(
                    part.strip()
                    for part in author_element.get_text().split("/")
                    if part.strip()
                )
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

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._soup: BeautifulSoup | None = None
        self._desktop_url = ""
        self._desktop_status_code: int | None = None
        self._mobile_status_code: int | None = None

        title_no_index = url.index("?title_no=") + len("?title_no=")
        title_no_end_index: int | None = url.find("&", title_no_index)
        if title_no_end_index == -1:  # if '&' not found in url
            title_no_end_index = None

        self._title_no = int(url[title_no_index:title_no_end_index])

        self._title_path = "/".join(url.split("/")[3:6])

    def fetch_metadata(self) -> BaseMetadata:
        page = self._get_desktop_soup()
        title = self._required_attribute(
            page,
            'meta[property="og:title"]',
            "content",
            "og:title meta content",
        )
        author_content = self._required_attribute(
            page,
            'meta[property="com-linewebtoon:webtoon:author"]',
            "content",
            "author meta content",
        )
        authors: list[str] = [
            author.strip() for author in author_content.split("/") if author.strip()
        ]
        summary = page.select_one("#content .summary")
        if summary is None:
            raise self._response_error(
                self._desktop_url,
                self._desktop_status_code,
                "missing #content .summary",
            )
        description = summary.get_text()

        meta_image = page.select_one('meta[property="og:image"]')

        if meta_image and "content" in meta_image.attrs:
            cover_art = str(meta_image["content"])
        else:
            cover_art_el = page.select_one("#content .detail_header.challenge img")
            cover_art = str(cover_art_el.get("src", "")) if cover_art_el else ""

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
        episode_list = self._get_chapters()
        chapters: list[BaseChapter] = []
        for index, episode in enumerate(episode_list, start=1):
            if not isinstance(episode, dict):
                raise self._response_error(
                    self._mobile_api_url,
                    self._mobile_status_code,
                    f"episodeList item {index} is not an object",
                )
            title = episode.get("episodeTitle")
            viewer_link = episode.get("viewerLink")
            if not isinstance(title, str) or not title:
                raise self._response_error(
                    self._mobile_api_url,
                    self._mobile_status_code,
                    f"episodeList item {index} has no episodeTitle",
                )
            if not isinstance(viewer_link, str) or not viewer_link:
                raise self._response_error(
                    self._mobile_api_url,
                    self._mobile_status_code,
                    f"episodeList item {index} has no viewerLink",
                )

            episode_number = episode.get("episodeNo")
            chapter_number = str(episode_number).strip() if episode_number is not None else ""
            if not chapter_number:
                chapter_number = str(index)
            chapters.append(
                BaseChapter(
                    title,
                    urljoin("https://www.webtoons.com", viewer_link),
                    chapter_number=chapter_number,
                )
            )
        return chapters

    def fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        soup = BeautifulSoup(requests.get(chapter.url).text, "lxml")
        images: list[str] = []
        for c in soup.select("div#_imageList > img"):
            images.append(c["data-url"])
        return images

    @property
    def _mobile_api_url(self) -> str:
        return (
            f"https://m.webtoons.com/api/v1/webtoon/{self._title_no}/episodes"
            "?pageSize=99999&cursor=0"
        )

    def _get_chapters(self) -> list[Any]:
        response = self._request(
            self._mobile_api_url,
            expected_content_type="application/json",
        )
        self._mobile_status_code = response.status_code
        try:
            data = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as error:
            raise self._response_error(
                self._mobile_api_url,
                response.status_code,
                f"invalid JSON: {error}",
            ) from error

        if not isinstance(data, dict):
            raise self._response_error(
                self._mobile_api_url,
                response.status_code,
                "response body is not an object",
            )
        result = data.get("result")
        if not isinstance(result, dict):
            raise self._response_error(
                self._mobile_api_url,
                response.status_code,
                "missing result object",
            )
        episode_list = result.get("episodeList")
        if not isinstance(episode_list, list):
            raise self._response_error(
                self._mobile_api_url,
                response.status_code,
                "missing episodeList array",
            )
        if not episode_list:
            raise self._response_error(
                self._mobile_api_url,
                response.status_code,
                "episodeList is empty",
            )
        return episode_list

    def _get_desktop_soup(self) -> BeautifulSoup:
        self._desktop_url = (
            f"https://www.webtoons.com/{self._title_path}/list?title_no={self._title_no}"
        )
        response = self._request(
            self._desktop_url,
            expected_content_type="text/html",
        )
        self._desktop_status_code = response.status_code
        return BeautifulSoup(response.text, "lxml")

    def _request(self, url: str, *, expected_content_type: str) -> requests.Response:
        headers = {**self.headers, "User-Agent": USER_AGENT}
        for attempt in range(self.max_request_attempts):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.request_timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
                if attempt + 1 < self.max_request_attempts:
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                raise self._response_error(url, None, f"network error: {error}") from error
            except requests.exceptions.RequestException as error:
                raise self._response_error(url, None, f"request failed: {error}") from error

            status_code = response.status_code
            if (status_code == 429 or 500 <= status_code <= 599) and (
                attempt + 1 < self.max_request_attempts
            ):
                time.sleep(self.retry_backoff_seconds * (2**attempt))
                continue

            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as error:
                raise self._response_error(
                    url,
                    status_code,
                    f"HTTP request failed: {error}",
                ) from error

            content_type = response.headers.get("Content-Type", "")
            if expected_content_type not in content_type.lower():
                raise self._response_error(
                    url,
                    status_code,
                    f"unexpected Content-Type {content_type!r}; expected {expected_content_type}",
                )
            return response

        raise AssertionError("Webtoons request retry loop ended unexpectedly")

    def _required_attribute(
        self,
        page: BeautifulSoup,
        selector: str,
        attribute: str,
        field_name: str,
    ) -> str:
        element = page.select_one(selector)
        value = element.get(attribute) if element else None
        if not isinstance(value, str) or not value:
            raise self._response_error(
                self._desktop_url,
                self._desktop_status_code,
                f"missing {field_name}",
            )
        return value

    def _response_error(
        self,
        url: str,
        status_code: int | None,
        reason: str,
    ) -> SourceResponseError:
        status = "unknown" if status_code is None else str(status_code)
        return SourceResponseError(
            f"Webtoons response error: url={url}, status={status}, "
            f"title_no={self._title_no}, reason={reason}"
        )

    @staticmethod
    def check_url(url: str) -> bool:
        return bool(
            re.match(r"https://www.webtoons.com/.*/list\?title_no=.*", url)
            or re.match(r"https://m.webtoons.com/.*/list\?title_no=.*", url)
            or re.match(r"https://www.webtoons.com/.*/viewer\?title_no=.*", url)
        )


def get_class() -> type[CommonSource]:
    return WebtoonsSource

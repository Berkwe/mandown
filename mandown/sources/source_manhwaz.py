"""
Source file for manhwaz.com
"""
# pylint: disable=invalid-name

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..base import BaseChapter, BaseMetadata
from ..request_utils import USER_AGENT
from .common_source import CommonSource


class ManhwaZSource(CommonSource):
    name = "ManhwaZ"
    domains = ["https://manhwaz.com"]
    headers = {"Referer": "https://manhwaz.com/", "User-Agent": USER_AGENT}

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._soup: BeautifulSoup | None = None

    def _fetch_metadata(self) -> BaseMetadata:
        soup = self._get_soup()

        title_el = soup.select_one(".post-title h1") or soup.select_one("h1")
        title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

        authors = self._get_authors(soup)
        genres = [
            clean_text(el.get_text(" ", strip=True)) for el in soup.select(".genres-content a")
        ]

        description_el = soup.select_one(".description-summary")
        description = (
            clean_text(description_el.get_text(" ", strip=True))
            if description_el
            else self._meta_content(soup, "og:description")
        )

        cover_art = self._meta_content(soup, "og:image")
        if not cover_art:
            cover_el = soup.select_one(".summary_image img")
            cover_art = cover_el.get("src", "") if cover_el else ""
        cover_art = self._normalize_asset_url(cover_art)

        canonical = soup.select_one('link[rel="canonical"]')
        source_url = canonical.get("href", self.url) if canonical else self.url

        return BaseMetadata(title, authors, source_url, genres, description, cover_art)

    def _fetch_chapter_list(self) -> list[BaseChapter]:
        soup = self._get_soup()

        chapters: list[BaseChapter] = []
        for link in soup.select(".wp-manga-chapter a"):
            href = link.get("href")
            if not href:
                continue

            title = clean_text(link.get_text(" ", strip=True))
            chapter_number = self._chapter_number_from_title(title)
            if not chapter_number:
                chapter_number = self._chapter_number_from_url(href)
            chapters.append(
                BaseChapter(
                    title,
                    urljoin(self.url, href),
                    chapter_number=chapter_number,
                )
            )

        chapters.reverse()
        return chapters

    def _fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        soup = BeautifulSoup(
            requests.get(chapter.url, headers=self.headers, timeout=20).text,
            "lxml",
        )

        images: list[str] = []
        for image in soup.select(
            ".reading-content img.chapter-img, #chapter_content img.chapter-img"
        ):
            src = image.get("data-src") or image.get("src")
            if src:
                images.append(self._normalize_asset_url(urljoin(chapter.url, src)))
        return images

    def _get_soup(self) -> BeautifulSoup:
        if self._soup:
            return self._soup

        self._soup = BeautifulSoup(
            requests.get(self.url, headers=self.headers, timeout=20).text,
            "lxml",
        )
        return self._soup

    @staticmethod
    def _meta_content(soup: BeautifulSoup, property_name: str) -> str:
        meta = soup.select_one(f'meta[property="{property_name}"]')
        return meta.get("content", "") if meta else ""

    @staticmethod
    def _get_authors(soup: BeautifulSoup) -> list[str]:
        for item in soup.select(".post-content_item"):
            text = clean_text(item.get_text(" ", strip=True))
            if text.startswith("Author(s)"):
                author = text.removeprefix("Author(s)").strip()
                return [author] if author else []
        return []

    @staticmethod
    def _chapter_number_from_title(title: str) -> str:
        match = re.search(r"Chapter\s+([0-9]+(?:\.[0-9]+)?)", title, re.IGNORECASE)
        return match.group(1) if match else ""

    @staticmethod
    def _chapter_number_from_url(url: str) -> str:
        match = re.search(r"/chapter-([0-9]+(?:\.[0-9]+)?)(?:/)?$", url)
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_asset_url(url: str) -> str:
        return url.replace(
            "https://manhwaz.com/home/manhwaz.com/public_html/public/storage/",
            "https://manhwaz.com/storage/",
        )

    @staticmethod
    def check_url(url: str) -> bool:
        return bool(re.match(r"https://(www\.)?manhwaz\.com/.*/webtoon/.*", url))


def get_class() -> type[CommonSource]:
    return ManhwaZSource


def clean_text(text: str) -> str:
    return " ".join(text.split())

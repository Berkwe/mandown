"""
Source file for mangadex.org
"""
# pylint: disable=invalid-name

import re
import subprocess
import time
from typing import Any

import requests
from bs4 import BeautifulSoup
from natsort import natsorted
from slugify import slugify

from ..base import BaseChapter, BaseMetadata
from ..errors import SourceResponseError
from ..request_utils import USER_AGENT
from .common_source import CommonSource


class MangaDexSource(CommonSource):
    name = "MangaDex"
    domains = ["https://mangadex.org"]

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._soup: BeautifulSoup | None = None
        self.lang_code = ""

        # https://api.mangadex.org/manga/de4b3c43-5243-4399-9fc3-68a3c0747138
        self.id = self.url.split("/")[4]
        if self.url.startswith("https://mangadex.org/chapter"):
            api_url = f"https://api.mangadex.org/chapter/{self.id}"
            payload = self._json_object(self._get(api_url), api_url)
            data = self._mapping(payload.get("data"))
            relationships = self._sequence(data.get("relationships"))
            manga_id = next(
                (
                    relationship.get("id")
                    for relationship in relationships
                    if isinstance(relationship, dict)
                    and relationship.get("type") == "manga"
                    and isinstance(relationship.get("id"), str)
                ),
                None,
            )
            if manga_id is None:
                raise self._response_error(api_url, "chapter has no manga relationship")
            self.id = manga_id

    def _fetch_metadata(self) -> BaseMetadata:
        # TODO: support non-English downloads
        api_url = (
            f"https://api.mangadex.org/manga/{self.id}"
            "?includes[]=author&includes[]=cover_art&includes[]=artist"
        )
        payload = self._json_object(self._get(api_url), api_url)

        metadata = self._mapping(payload.get("data"))
        attributes = self._mapping(metadata.get("attributes"))
        titles = self._mapping(attributes.get("title"))
        available_languages = {
            language
            for language in self._sequence(attributes.get("availableTranslatedLanguages"))
            if isinstance(language, str)
        }

        # use english if possible, otherwise use the first language that appears
        if "en" in available_languages or isinstance(titles.get("en"), str):
            self.lang_code = "en"
        else:
            self.lang_code = next(
                (
                    key
                    for key, value in titles.items()
                    if isinstance(key, str) and isinstance(value, str)
                ),
                "",
            )

        title = self._metadata_title(attributes, titles)
        description = self._localized_string(attributes.get("description"), self.lang_code)
        # strip trailing spaces on each line
        description = "\n".join(line.strip() for line in description.split("\n"))

        authors, cover_art = self._metadata_relationships(metadata)
        genres = self._metadata_genres(attributes)

        return BaseMetadata(
            title,
            list(authors),
            f"https://mangadex.org/title/{self.id}",
            genres,
            description,
            cover_art,
        )

    def _metadata_title(self, attributes: dict, titles: dict) -> str:
        for alt_title in self._sequence(attributes.get("altTitles")):
            if isinstance(alt_title, dict):
                candidate = alt_title.get(self.lang_code)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
        return self._localized_string(titles, self.lang_code) or self.id

    def _metadata_relationships(self, metadata: dict) -> tuple[set[str], str]:
        authors: set[str] = set()
        cover_art = ""
        for relationship in self._sequence(metadata.get("relationships")):
            if not isinstance(relationship, dict):
                continue
            relation_type = relationship.get("type")
            relation_attributes = self._mapping(relationship.get("attributes"))
            if relation_type in {"author", "artist"}:
                name = relation_attributes.get("name")
                if isinstance(name, str) and name:
                    authors.add(name)
            elif relation_type == "cover_art":
                file_name = relation_attributes.get("fileName")
                if isinstance(file_name, str) and file_name:
                    cover_art = (
                        f"https://uploads.mangadex.org/covers/{self.id}/{file_name}"
                    )
        return authors, cover_art

    def _metadata_genres(self, attributes: dict) -> list[str]:
        genres: list[str] = []
        for tag_data in self._sequence(attributes.get("tags")):
            if not isinstance(tag_data, dict):
                continue
            tag_attributes = self._mapping(tag_data.get("attributes"))
            if tag_attributes.get("group") not in {"genre", "theme"}:
                continue
            tag_name = self._localized_string(tag_attributes.get("name"), self.lang_code)
            if tag_name:
                genres.append(tag_name)
        return genres

    def _fetch_chapter_list(self) -> list[BaseChapter]:
        preferred_chapters = self._fetch_chapter_feed(self.lang_code)
        chapters_by_number = {self._chapter_number_key(c): c for c in preferred_chapters}

        # MangaDex's title page shows the aggregate chapter list. If the preferred
        # language has gaps, fill those chapter numbers from the full feed.
        aggregate_count = self._fetch_aggregate_chapter_count()
        if aggregate_count > len(chapters_by_number):
            for c in self._fetch_chapter_feed():
                chapters_by_number.setdefault(self._chapter_number_key(c), c)

        chapter_data = natsorted(
            chapters_by_number.values(),
            key=self._chapter_sort_key,
        )

        chapters: list[BaseChapter] = []
        for c in chapter_data:
            attributes = self._mapping(c.get("attributes"))
            chapter_number_value = attributes.get("chapter")
            chapter_number = (
                chapter_number_value if isinstance(chapter_number_value, str) else ""
            )
            chapter_title_value = attributes.get("title")
            chapter_title = (
                chapter_title_value
                if isinstance(chapter_title_value, str) and chapter_title_value
                else f"Chapter {chapter_number}"
            )
            chapter_slug: str = self._chapter_slug(chapter_number, chapter_title)
            chapters.append(
                BaseChapter(
                    chapter_title,
                    f"https://mangadex.org/chapter/{c['id']}",
                    chapter_slug,
                    chapter_number,
                )
            )
        return chapters

    @staticmethod
    def _chapter_slug(chapter_number: str, chapter_title: str) -> str:
        title_slug = slugify(chapter_title).strip()
        numeric_chapter_number = BaseChapter.parse_chapter_number(chapter_number)
        if numeric_chapter_number is None:
            return title_slug

        chapter_number_slug = str(numeric_chapter_number).replace(".", "-").rjust(5, "0")
        return f"{chapter_number_slug}. {title_slug}"

    def _fetch_chapter_feed(self, lang_code: str | None = None) -> list[dict]:
        chapters: list[dict] = []
        offset = 0
        limit = 500
        while True:
            lang_param = f"&translatedLanguage[]={lang_code}" if lang_code else ""
            api_url = (
                f"https://api.mangadex.org/manga/{self.id}/"
                f"feed?limit={limit}&offset={offset}{lang_param}"
                "&order[volume]=asc&order[chapter]=asc"
            )
            payload = self._json_object(self._get(api_url), api_url)
            data = self._sequence(payload.get("data"))
            for chapter in data:
                if not isinstance(chapter, dict):
                    continue
                chapter_id = chapter.get("id")
                attributes = chapter.get("attributes")
                if isinstance(chapter_id, str) and isinstance(attributes, dict):
                    chapters.append(chapter)

            offset += len(data)
            total = payload.get("total")
            if not data:
                break
            if isinstance(total, int):
                if offset >= total:
                    break
            elif len(data) < limit:
                break
        return chapters

    def _fetch_aggregate_chapter_count(self) -> int:
        api_url = f"https://api.mangadex.org/manga/{self.id}/aggregate"
        payload = self._json_object(self._get(api_url), api_url)
        volumes = payload.get("volumes")
        if not isinstance(volumes, dict):
            return 0
        return sum(
            len(chapters)
            for volume in volumes.values()
            if isinstance(volume, dict)
            and isinstance((chapters := volume.get("chapters")), dict)
        )

    @staticmethod
    def _chapter_number_key(chapter: dict) -> tuple[str, str, str]:
        attributes = MangaDexSource._mapping(chapter.get("attributes"))
        chapter_number = attributes.get("chapter")
        chapter_id = chapter.get("id")
        if not isinstance(chapter_id, str):
            chapter_id = ""
        if not isinstance(chapter_number, str) or not chapter_number:
            return ("", "", chapter_id)
        volume = attributes.get("volume")
        return (volume if isinstance(volume, str) else "", chapter_number, "")

    @staticmethod
    def _chapter_sort_key(chapter: dict) -> tuple[str, str, str]:
        attributes = MangaDexSource._mapping(chapter.get("attributes"))
        volume = attributes.get("volume")
        chapter_number = attributes.get("chapter")
        chapter_id = chapter.get("id")
        return (
            volume if isinstance(volume, str) else "",
            chapter_number if isinstance(chapter_number, str) else "",
            chapter_id if isinstance(chapter_id, str) else "",
        )

    def _fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        *_, chapter_id = chapter.url.split("/")
        api_url = f"https://api.mangadex.org/at-home/server/{chapter_id}"
        payload = self._json_object(self._get(api_url), api_url)
        base_url = payload.get("baseUrl")
        chapter_data = self._mapping(payload.get("chapter"))
        chapter_hash = chapter_data.get("hash")
        pages = chapter_data.get("data")
        if not isinstance(base_url, str) or not base_url:
            raise self._response_error(api_url, "missing baseUrl")
        if not isinstance(chapter_hash, str) or not chapter_hash:
            raise self._response_error(api_url, "missing chapter.hash")
        if not isinstance(pages, list):
            raise self._response_error(api_url, "missing chapter.data array")

        return [
            f"{base_url}/data/{chapter_hash}/{page}"
            for page in pages
            if isinstance(page, str) and page
        ]

    @staticmethod
    def _mapping(value: Any) -> dict:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _sequence(value: Any) -> list:
        return value if isinstance(value, list) else []

    @staticmethod
    def _localized_string(value: Any, lang_code: str) -> str:
        values = MangaDexSource._mapping(value)
        localized = values.get(lang_code) or values.get("en")
        if isinstance(localized, str) and localized.strip():
            return localized
        return next(
            (
                candidate
                for candidate in values.values()
                if isinstance(candidate, str) and candidate.strip()
            ),
            "",
        )

    @staticmethod
    def _json_object(response: requests.Response, url: str) -> dict:
        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as error:
            raise MangaDexSource._response_error(url, f"invalid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise MangaDexSource._response_error(url, "response body is not an object")
        return payload

    @staticmethod
    def _response_error(url: str, reason: str) -> SourceResponseError:
        return SourceResponseError(f"MangaDex response error: url={url}, reason={reason}")

    @staticmethod
    def check_url(url: str) -> bool:
        return bool(
            re.match(r"https://mangadex.org/title/.*", url)
            or re.match(r"https://mangadex.org/chapter/.*", url)
        )

    @staticmethod
    def _get(url: str) -> requests.Response:
        """
        A wrapper of requests.get for MangaDex with
        rudimentary rate-limit processing
        """
        for _ in range(3):
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=5)
            if r.status_code == 400 and "Unsupported Browser" in r.text:
                r = MangaDexSource._get_with_curl(url)
            if r.status_code == 200:
                break
            elif r.status_code == 404:
                raise RuntimeError(
                    "This chapter is not downloadable from MangaDex. If you "
                    "believe this to be an error, please open a GitHub issue."
                )
            time.sleep(1)
        else:
            raise RuntimeError("MangaDex is probably rate-limiting us, try again later?")
        return r

    @staticmethod
    def _get_with_curl(url: str) -> requests.Response:
        """
        MangaDex currently rejects Python requests' TLS/browser fingerprint in
        some regions. curl gets the same public response as a regular browser.
        """
        completed = subprocess.run(
            [
                "curl",
                "-sS",
                "-L",
                "--globoff",
                "--max-time",
                "20",
                "-w",
                "\n%{http_code}",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        response = requests.Response()
        response.url = url
        response.encoding = "utf-8"

        if completed.returncode != 0:
            response.status_code = 0
            response._content = completed.stderr.encode()  # pylint: disable=protected-access
            return response

        body, _, status_code = completed.stdout.rpartition("\n")
        response.status_code = int(status_code)
        response._content = body.encode()  # pylint: disable=protected-access
        return response


def get_class() -> type[CommonSource]:
    return MangaDexSource

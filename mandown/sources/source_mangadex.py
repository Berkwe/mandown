"""
Source file for mangadex.org
"""
# pylint: disable=invalid-name

import re
import subprocess
import time

import requests
from bs4 import BeautifulSoup
from natsort import natsorted
from slugify import slugify

from ..base import BaseChapter, BaseMetadata
from ..request_utils import USER_AGENT
from .base_source import SourceSearchResult
from .common_source import CommonSource


class MangaDexSource(CommonSource):
    name = "MangaDex"
    domains = ["https://mangadex.org"]

    @classmethod
    def search(cls, title: str) -> list[SourceSearchResult]:
        response = cls._get(
            "https://api.mangadex.org/manga"
            f"?title={requests.utils.quote(title)}"
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

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._soup: BeautifulSoup | None = None
        self.lang_code = ""

        # https://api.mangadex.org/manga/de4b3c43-5243-4399-9fc3-68a3c0747138
        self.id = self.url.split("/")[4]
        if self.url.startswith("https://mangadex.org/chapter"):
            r: dict = self._get(f"https://api.mangadex.org/chapter/{self.id}").json()["data"][
                "relationships"
            ]
            self.id: str = next(filter(lambda i: i["type"] == "manga", r))["id"]  # type: ignore

    def _fetch_metadata(self) -> BaseMetadata:
        # TODO: support non-English downloads
        r = self._get(
            f"https://api.mangadex.org/manga/{self.id}"
            "?includes[]=author&includes[]=cover_art&includes[]=artist"
        ).json()

        metadata: dict = r["data"]

        # use english if possible, otherwise use the first language that appears
        self.lang_code = (
            "en"
            if "en" in metadata["attributes"]["availableTranslatedLanguages"]
            else next(iter(metadata["attributes"]["title"]))
        )
        title: str | None = None
        for AltTitle in metadata["attributes"]["altTitles"]:
            if self.lang_code in AltTitle.keys():
                title = AltTitle[self.lang_code]
                break
        if title is None:
            for wt_lang_code, wt_title in metadata["attributes"]["title"].items():
                title = wt_title
                break
        if metadata["attributes"]["description"]:
            description: str = metadata["attributes"]["description"][self.lang_code]

            # strip trailing spaces on each line
            description = "\n".join(s.strip() for s in description.split("\n"))
        else:
            description = ""

        authors: set[str] = set()
        cover_art = ""
        for d in metadata["relationships"]:
            if d["type"] == "author" or d["type"] == "artist":
                authors.add(d["attributes"]["name"])
            elif d["type"] == "cover_art":
                # pylint: disable=line-too-long
                cover_art = (
                    f"https://uploads.mangadex.org/covers/{self.id}/{d['attributes']['fileName']}"
                )

        genres: list[str] = []
        for d in metadata["attributes"]["tags"]:
            tag = d["attributes"]["group"]
            if tag == "genre" or tag == "theme":
                genres.append(d["attributes"]["name"][self.lang_code])

        return BaseMetadata(
            title,
            list(authors),
            f"https://mangadex.org/title/{self.id}",
            genres,
            description,
            cover_art,
        )

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
            key=lambda c: (
                c["attributes"].get("volume") or "",
                c["attributes"].get("chapter") or "",
                c["id"],
            ),
        )

        chapters: list[BaseChapter] = []
        for c in chapter_data:
            chapter_number = c["attributes"]["chapter"] or ""
            chapter_title: str = c["attributes"]["title"] or f"Chapter {chapter_number}"
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
            r = self._get(
                f"https://api.mangadex.org/manga/{self.id}/"
                f"feed?limit={limit}&offset={offset}{lang_param}"
                "&order[volume]=asc&order[chapter]=asc"
            ).json()
            chapters.extend(r["data"])

            offset += len(r["data"])
            if offset >= r["total"] or not r["data"]:
                break
        return chapters

    def _fetch_aggregate_chapter_count(self) -> int:
        r = self._get(f"https://api.mangadex.org/manga/{self.id}/aggregate").json()
        return sum(len(v.get("chapters", {})) for v in r.get("volumes", {}).values())

    @staticmethod
    def _chapter_number_key(chapter: dict) -> tuple[str, str, str]:
        attributes = chapter["attributes"]
        chapter_number = attributes.get("chapter")
        if chapter_number is None:
            return ("", "", chapter["id"])
        return (attributes.get("volume") or "", chapter_number, "")

    def _fetch_chapter_image_list(self, chapter: BaseChapter) -> list[str]:
        *_, chapter_id = chapter.url.split("/")
        r = self._get(f"https://api.mangadex.org/at-home/server/{chapter_id}").json()
        base_url = r["baseUrl"]
        chapter_hash = r["chapter"]["hash"]

        images: list[str] = []
        for p in r["chapter"]["data"]:
            images.append(f"{base_url}/data/{chapter_hash}/{p}")
        return images

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

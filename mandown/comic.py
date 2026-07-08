from . import sources
from .base import BaseChapter, BaseMetadata
from .sources.base_source import BaseSource


class BaseComic:
    """
    A comic with metadata and chapter data.

    :param `metadata`: Metadata of the comic
    :param `chapters`: A list of chapters of the comic
    """

    def __init__(
        self,
        metadata: BaseMetadata,
        chapters: list[BaseChapter],
    ):
        self.metadata = metadata
        self.chapters = chapters
        BaseChapter.sync_slug_order(self.chapters)

        try:
            self.source = sources.get_class_for(self.metadata.url)(self.metadata.url)
        except ValueError as err:
            if self.metadata.url == "":  # sentinel value
                self.source = BaseSource("")
            else:
                raise ValueError from err

    def asdict(self) -> dict:
        """
        Return a dictionary representation of the comic.
        """

        return {
            "metadata": self.metadata.asdict(),
            "chapters": [c.asdict() for c in self.chapters],
        }

    def get_chapter_image_urls(self, chapter: BaseChapter) -> list[str]:
        """
        Fetch a list of image URLs of a chapter based on the
        current source.

        :param `chapter`: The chapter to fetch
        :return: A list of image URLs
        """
        return self.source.fetch_chapter_image_list(chapter)

    def set_chapter_range(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        by_chapter_number: bool = False,
    ) -> None:
        """
        `start` and `end` are zero-indexed unless `by_chapter_number` is True.

        :param `start`: The index of the first chapter to keep
        :param `end`: The index of the last chapter to keep (exclusive)
        :param `by_chapter_number`: Match against the source's real chapter number
        instead of the chapter's position in the fetched list. When enabled, `end`
        is inclusive.
        """
        if by_chapter_number:
            self._set_chapter_number_range(start=start, end=end)
            return

        self.chapters = self.chapters[start:end]

    def _set_chapter_number_range(self, *, start: int | None, end: int | None) -> None:
        if start is None and end is None:
            return

        numbered_chapters = [
            (chapter, chapter.numeric_chapter_number)
            for chapter in self.chapters
            if chapter.numeric_chapter_number is not None
        ]
        if not numbered_chapters:
            raise ValueError("This comic does not expose real chapter numbers.")

        start_number = BaseChapter.parse_chapter_number(str(start)) if start is not None else None
        end_number = BaseChapter.parse_chapter_number(str(end)) if end is not None else None

        available_numbers = {number for _, number in numbered_chapters}
        if start_number is not None and start_number not in available_numbers:
            raise ValueError(f"Chapter {start} was not found.")
        if end_number is not None and end_number not in available_numbers:
            raise ValueError(f"Chapter {end} was not found.")

        selected = []
        for chapter, chapter_number in numbered_chapters:
            if start_number is not None and chapter_number < start_number:
                continue
            if end_number is not None and chapter_number > end_number:
                continue
            selected.append(chapter)

        if not selected:
            raise ValueError(f"No chapters found in range {start} to {end}.")

        self.chapters = selected

    def update(self, *, chapters: bool = True, metadata: bool = True) -> None:
        """
        Refresh comic.metadata and comic.chapters with new information
        from the source. Remember to call mandown.save_metadata(comic)
        to actually save the updated data to the filesystem.

        :param `chapters`: whether to update the chapter index
        :param `metadata`: whether to update comic metadata
        """
        if chapters:
            self.chapters = self.source.fetch_chapter_list()
            BaseChapter.sync_slug_order(self.chapters)

        if metadata:
            self.metadata = self.source.fetch_metadata()

    def __str__(self) -> str:
        return f"""
Title: {self.metadata.title},
Author(s): {", ".join(self.metadata.authors)}
URL: {self.metadata.url}
Genres: {", ".join(self.metadata.genres)}
Chapters: {len(self.chapters)}
Description:
    {self.metadata.description}
""".strip()

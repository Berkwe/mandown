# pylint: disable=invalid-name

import shutil
import threading
from pathlib import Path
from typing import Callable, Iterator

import comicon

from . import io, sources
from .base import Progress
from .comic import BaseComic
from .convert_utils import ConvertFormats, convert_one
from .errors import ChapterImageCountMismatchError, ImageDownloadError
from .processor import ProcessConfig, ProcessOps, Processor
from .search import SEARCH_PROVIDERS, SearchItem, SearchResults

DEFAULT_SEARCH_SOURCES = ("naver", "webtoons", "mangadex")


def query(url: str) -> BaseComic:
    """
    Attempt to query for a comic given a URL.
    :param `url`: An internet URL to search for
    :raises `ValueError` if the source is not found.
    """
    adapter = sources.get_class_for(url)(url)
    return BaseComic(adapter.metadata, adapter.chapters)


def search(title: str, source: str | None = None) -> SearchResults:
    """
    Search Naver Webtoon, WEBTOON, and MangaDex for a series title.

    The returned mapping always contains the keys ``naver``, ``webtoons``,
    ``mangadex``, and ``anilist``. AniList is available through
    ``source="anilist"`` but is not queried by default; use
    :func:`mandown.search_all` for concurrent search with WEBTOON results
    derived from AniList external links. Unqueried catalogs remain ``None``.
    Each value is an ordered list of lightweight :class:`SearchItem` objects,
    or ``None`` when that catalog has no matches.
    Search results do not fetch every chapter immediately; access
    ``item.comic`` to load the selected result as a full :class:`BaseComic`.

    Example::

        results = mandown.search("solo leveling")
        matches = results["mangadex"]
        if matches:
            comic = matches[0].comic

    :param title: Series title or search phrase. Leading and trailing whitespace
        is ignored.
    :param source: Optional source key: ``"naver"``, ``"webtoons"``,
        ``"mangadex"``, or ``"anilist"``. Matching is case-insensitive.
    :returns: Search results grouped by supported catalog.
    :raises ValueError: If ``title`` is empty or ``source`` is unsupported.
    """
    if not title or not title.strip():
        raise ValueError("Search title cannot be empty.")
    source_key = source.strip().lower() if source is not None else None
    if source_key is not None and source_key not in SEARCH_PROVIDERS:
        supported = ", ".join(SEARCH_PROVIDERS)
        raise ValueError(f"Unsupported search source {source!r}. Choose one of: {supported}.")

    # Ask each supported catalog to search using its own native search endpoint.
    # Keep the matches lightweight here; the full comic and its chapters are
    # fetched only when the caller accesses SearchItem.comic.
    output = SearchResults()
    for source_name, provider in SEARCH_PROVIDERS.items():
        should_search = (
            source_name == source_key
            if source_key is not None
            else source_name in DEFAULT_SEARCH_SOURCES
        )
        if not should_search:
            output[source_name] = None
            continue
        matches = [SearchItem(source_name, match) for match in provider(title.strip())]

        # A missing result is represented by None so callers can distinguish it
        # directly from a catalog that returned one or more ordered matches.
        output[source_name] = matches or None
    return output


def load(path: Path | str) -> BaseComic:
    """
    Load a mandown-created comic from the file system.

    :param `path`: A folder where mandown has created a comic
    :returns A comic with metadata and chapter data of that folder

    :raises FileNotFoundError if `md-metadata.json` cannot be found
    :raises IOError if the path does not exist
    """
    return io.read_comic(path)


def save_metadata(comic: BaseComic, path: Path | str) -> None:
    """
    Save the metadata from the comic to `<path>/md-metadata.json`.

    :param `comic`: A comic with metadata to save
    :param `path`: A folder to save the metadata to
    """
    io.save_comic(comic, path)


def init_parse_comic(
    path: Path | str, donor_comic: BaseComic | None = None, download_cover: bool = False
) -> BaseComic:
    """
    Open a comic from a folder path, either via `md-metadata.json` or
    if that fails, parse the comic structure and create an `md-metadata.json`

    :param `path`: A folder containing `md-metadata.json` or a comic structure
    :param `data`: A comic to fill metadata from if no metadata is found
    :param `download_cover`: If `True` and `source_url` is set, download
    the cover image if no metadata is found
    :returns A comic with metadata and chapter data of that folder
    :raises `AttributeError` if the source URL is not set and `download_cover` is `True`
    """
    if not donor_comic and download_cover:
        raise AttributeError("Cannot download cover without donor comic")

    try:
        comic = io.read_comic(path)
    except FileNotFoundError:
        comic = io.parse_comic(path, donor_comic)
        io.save_comic(comic, path)

        if download_cover and comic.metadata.cover_art:
            next(
                io.download_images(
                    [comic.metadata.cover_art],
                    path,
                    filestems=["cover"],
                    headers=comic.source.headers,
                )
            )
    return comic


def convert_progress(
    comic_path: Path | str,
    to: ConvertFormats,
    dest_folder: Path | str | None = None,
    remove_after: bool = False,
    split_by_chapters: bool = False,
) -> Iterator[str | int]:
    """
    Convert the comic located at `folder_path` to `convert_to`
    and put it in `dest_folder` (defaults to workdir).

    :param `comic_path`: The path to the comic to convert (may be in Mandown
    folder form or any of the `mandown.ConvertFormats` such as EPUB)
    :param `convert_to`: The format to convert to
    :param `dest_folder`: A folder to put the converted comic in
    :param `remove_after`: If `True`, delete the original file/folder after conversion
    :param `split_by_chapters`: Only applies to Mandown-created comics. If `True`,
    output a comic file per chapter. Existing comic files will not be overwritten.

    :returns An `Iterator` representing a progress bar. The first iteration returns
    the remaining number of iterations. If converting between file formats, an
    iteration after the first number of iterations ends will return the remaining
    number of the second number of iterations.
    """
    comic_path = Path(comic_path)
    if to == ConvertFormats.NONE:
        return

    # default to working directory
    dest_folder = Path(dest_folder or ".").resolve()

    if comic_path.is_dir():
        # it's a mandown comic, convert it to CIR
        comic = load(comic_path)

        # find cover
        cover: str | None = None
        for item in comic_path.iterdir():
            if item.name.startswith("cover"):
                cover = item.name
                break

        if split_by_chapters:
            comicon_comics = [
                comicon.Comic(
                    comicon.Metadata(
                        # pad to 5 leading zeroes
                        title=f"{comic.metadata.title} #{i:05}: {chap.title}",
                        authors=comic.metadata.authors,
                        description=comic.metadata.description,
                        genres=comic.metadata.genres,
                        cover_path_rel=cover,
                    ),
                    [comicon.Chapter(chap.title, chap.slug)],
                )
                for i, chap in enumerate(comic.chapters, start=1)
            ]

            yield len(comicon_comics)
            existing_filenames = {file.name for file in dest_folder.iterdir() if file.is_file()}
            for comicomic in comicon_comics:
                yield comicomic.metadata.title

                # do not overwrite existing cache
                if f"{comicomic.metadata.title_slug}.{to.value}" in existing_filenames:
                    continue
                for _ in convert_one(comicomic, comic_path, to, dest_folder):
                    ...

        else:
            comicon_comic = comicon.Comic(
                comicon.Metadata(
                    title=comic.metadata.title,
                    authors=comic.metadata.authors,
                    description=comic.metadata.description,
                    genres=comic.metadata.genres,
                    cover_path_rel=cover,
                ),
                [comicon.Chapter(chap.title, chap.slug) for chap in comic.chapters],
            )

            yield from convert_one(comicon_comic, comic_path, to, dest_folder)

    else:
        # it's a file, no conversion needed, let comicon do its inferencing
        yield from comicon.convert_progress(
            comic_path, dest_folder / f"{comic_path.stem}.{to.value}"
        )

    if remove_after:
        shutil.rmtree(comic_path)


def convert(
    comic_path: Path | str,
    to: ConvertFormats,
    dest_folder: Path | str | None = None,
    remove_after: bool = False,
) -> None:
    """
    Convert the comic located at `folder_path` to `convert_to`
    and put it in `dest_folder` (defaults to workdir).

    :param `comic_path`: The path to the comic to convert (may be in Mandown
    folder form or any of the `mandown.ConvertFormats` such as EPUB)
    :param `convert_to`: The format to convert to
    :param `dest_folder`: A folder to put the converted comic in
    :param `remove_after`: If `True`, delete the original file/folder after conversion
    """
    for _ in convert_progress(comic_path, to, dest_folder, remove_after):
        pass


def process_progress(
    comic_path: Path | str, ops: list[ProcessOps], config: ProcessConfig | None = None
) -> Iterator[str]:
    """
    Process the comic in `comic_path` with `ops` in the order provided.

    :param `comic_path`: A folder containing a image folders to process
    :param `ops`: A list of operations to perform on each image
    :param `config`: Options for processing operations
    :returns An `Iterator` representing a progress bar up to the number of images in the comic.
    """
    data = io.discover_local_images(comic_path)
    for _, images in data.items():
        for i in images:
            Processor(i, config).process(ops)
        yield "Processing"


def process(
    comic_path: Path | str, ops: list[ProcessOps], config: ProcessConfig | None = None
) -> None:
    """
    Process the comic in `comic_path` with `ops` in the order provided.

    :param `comic_path`: A folder containing a image folders to process
    :param `ops`: A list of operations to perform on each image
    :param `config`: Options for processing operations
    """
    for _ in process_progress(comic_path, ops, config):
        pass


def _use_chapter_number_range(comic: BaseComic) -> bool:
    return any(chapter.chapter_number for chapter in comic.chapters)


def download_progress(
    comic: BaseComic | str,
    path: Path | str = ".",
    *,
    start: int | None = None,
    end: int | None = None,
    threads: int = 4,
    only_download_missing: bool = True,
    raise_on_failed_download: bool = True,
    panel_size: io.PanelSize | None = None,
    image_format: str | None = None,
    main_progress: Progress = None,
    chapter_progress: Progress = None,
    progress_callback: Callable[[], None] | None = None
) -> Iterator[str]:
    """
    Download comic or comic URL `comic` to `path` using `threads` threads.

    :param `comic`: A comic or URL to download
    :param `path`: A folder to download the comic to
    :param `start`: The first chapter to download (zero-indexed, inclusive).
    For MangaDex, this is the real chapter number instead.
    :param `end`: The last chapter to download (zero-indexed, exclusive).
    For MangaDex, this is the real chapter number and is inclusive.
    :param `threads`: The number of threads to use
    :param `only_download_missing`: If `True`, do not download
    images already in the destination path
    :param `panel_size`: If provided as `(height, width)`, split each downloaded
    image into fixed-size panels after resizing it to the target width
    :param `image_format`: If provided as `jpg` or `png`, convert downloaded images
    to that format

    :returns An `Iterator` representing a progress bar up to the number of chapters in the comic.
    """
    path = Path(path)
    image_format = io.normalize_image_format(image_format)

    # make var comic a BaseComic
    if isinstance(comic, str):
        comic = query(comic)

    comic.set_chapter_range(
        start=start,
        end=end,
        by_chapter_number=_use_chapter_number_range(comic),
    )

    full_path = path / comic.metadata.title_slug
    full_path.mkdir(exist_ok=True)

    # save metadata json
    io.save_comic(comic, full_path)

    # cover
    if comic.metadata.cover_art:
        for _ in io.download_images(
            [comic.metadata.cover_art],
            full_path,
            filestems=["cover"],
            headers=comic.source.headers,
            image_format=image_format,
        ):
            pass

    # for each chapter
    for i, chap in enumerate(comic.chapters):
        yield chap.title
        image_urls = comic.get_chapter_image_urls(chap)
        chapter_path = full_path / chap.slug
        chapter_path.mkdir(exist_ok=True)
        if main_progress is not None:
            main_progress.total = len(comic.chapters)
            main_progress.current = i
            main_progress.progress = (
                round((100 / main_progress.total * main_progress.current), 1)
                if main_progress.total
                else 0
            )
            if progress_callback is not None:
                progress_callback()
        # expect that they're named by numbers only
        skip_images: set[int] = set()
        if panel_size is not None:
            io.clear_numbered_images(chapter_path)
        elif only_download_missing:
            for file in chapter_path.iterdir():
                if file.stem == file.stem.rjust(io.NUM_LEFT_PAD_DIGITS, "0"):
                    try:
                        skip_images.add(int(file.stem))
                    except ValueError:
                        # expected if not an image file
                        pass

        if not image_urls:
            # move to next chapter if there's nothing to download for this one
            continue

        if len(skip_images) == len(image_urls):
            continue

        # name them 00001.png, 00002.png, etc
        # skipping ones that already exist
        try:
            processed_image_urls, filestems = zip(
                *(
                    (link, str(i).rjust(io.NUM_LEFT_PAD_DIGITS, "0"))
                    for i, link in enumerate(image_urls, start=1)
                    if i not in skip_images
                ),
                strict=False,
            )
        except ValueError:
            # ValueError is raised when `zip` is given no arguments and thus
            # no images to download
            processed_image_urls, filestems = [], []

            raise ChapterImageCountMismatchError(
                "There are more images in the filesystem than in present in the chapter index."
                " You should never see this message."
            ) from None

        chapter_path = full_path / chap.slug
        if chapter_progress is not None:
            chapter_progress.total = len(processed_image_urls)
        image_downloader = (
            io.download_images_as_panels(
                processed_image_urls,
                chapter_path,
                headers=comic.source.headers,
                threads=threads,
                panel_size=panel_size,
                image_format=image_format,
            )
            if panel_size is not None
            else io.download_images(
                processed_image_urls,
                chapter_path,
                headers=comic.source.headers,
                filestems=filestems,
                threads=threads,
                panel_size=None,
                image_format=image_format,
            )
        )
        for _ in image_downloader:
            if chapter_progress is not None:
                chapter_progress.current += 1
                chapter_progress.progress = (
                    round(((100 / chapter_progress.total) * chapter_progress.current), 1)
                    if chapter_progress.total
                    else 0
                )
                if progress_callback is not None:
                    progress_callback()
        if chapter_progress is not None:
            chapter_progress.current = 0
            chapter_progress.progress = 0
            chapter_progress.total = 0

        # check if every image was downloaded
        expected_count = len(processed_image_urls) if panel_size is None else None
        image_count = len([f for f in chapter_path.iterdir() if f.is_file()])
        if expected_count is not None and (count := image_count) != expected_count:
            if raise_on_failed_download:
                raise ImageDownloadError(
                    f"Failed to download {len(processed_image_urls) - count} images"
                )
    if main_progress is not None:
        main_progress.current = main_progress.total
        main_progress.progress = 100
        if progress_callback is not None:
            progress_callback()


def download(
    comic: BaseComic | str,
    path: Path | str = ".",
    *,
    start: int | None = None,
    end: int | None = None,
    threads: int = 4,
    only_download_missing: bool = True,
    raise_on_failed_download: bool = True,
    panel_size: io.PanelSize = (1280, 800),
    image_format: str | None = None,
    progress_callback: Callable[[Progress, Progress], None] | None = None
    ):
    """
    Download comic or comic URL `comic` to `path` using `threads` threads.

    :param `comic`: A comic or URL to download
    :param `path`: A folder to download the comic to
    :param `start`: The first chapter to download (one-indexed, inclusive)
    :param `end`: The last chapter to download (one-indexed, inclusive)
    :param `threads`: The number of threads to use
    :param `only_download_missing`: If `True`, do not download images
    already in the destination path
    :param `panel_size`: If provided as `(height, width)`, split each downloaded
    image into fixed-size panels after resizing it to the target width
    :param `image_format`: If provided as `jpg` or `png`, convert downloaded images
    to that format

    :param `progress_callback`: Optional callback invoked every time `main_progress`
    or `chapter_progress` changes. It receives no arguments — read the updated
    state directly from the `main_progress` and `chapter_progress` objects
    returned by `download()`.

    Main_progress and Chapter_progress are instances of the mandown.base.Progress class.
    They contain:
    progress: progress as a percentage
    total: total number of segments
    current: the last processed segment

    Example:
    ```python
        main_progress, chapter_progress, thread = download(comic, progress_callback=lambda: None)

        def on_progress():
            print(f"Main Progress : {main_progress.current}/{main_progress.total}, "
                f"Chapter Progress : {chapter_progress.progess}")
    ```
    """
    main_progress = Progress()
    chapter_progress = Progress()

    def _wrapped_callback():
        if progress_callback is not None:
            progress_callback(main_progress, chapter_progress)


    def _run():
        for _ in download_progress(
            comic, path,
            start=start, end=end, threads=threads,
            only_download_missing=only_download_missing,
            raise_on_failed_download=raise_on_failed_download,
            panel_size=panel_size,
            image_format=image_format,
            main_progress=main_progress,
            chapter_progress=chapter_progress,
            progress_callback=_wrapped_callback
        ):
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join()
    return main_progress, chapter_progress, thread

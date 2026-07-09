import json
import multiprocessing as mp
import os
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from time import sleep
from typing import Iterator, Sequence

import filetype
import requests as RealRequests
from natsort import natsorted

from .base import BaseChapter, BaseMetadata
from .comic import BaseComic

NUM_LEFT_PAD_DIGITS = 5
FILE_PADDING = f"0{NUM_LEFT_PAD_DIGITS}"
MD_METADATA_FILE = "md-metadata.json"

PanelSize = tuple[int, int]
AsyncDownloadImageInput = tuple[
    str,
    Path | str,
    str | None,
    dict[str, str] | None,
    PanelSize | None,
]


def resize_image_to_panel_size(image_path: Path, panel_size: PanelSize) -> None:
    try:
        from PIL import Image, ImageOps
    except ImportError as err:
        raise ImportError(
            "Pillow was not found and is needed for panel_size resizing. Is it installed?"
        ) from err

    height, width = panel_size
    if height <= 0 or width <= 0:
        raise ValueError("panel_size must be a tuple of positive integers: (height, width)")

    target_size = (width, height)
    with Image.open(image_path) as image:
        if image.size == target_size:
            return

        resized = ImageOps.fit(
            image,
            target_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        if image_path.suffix.lower() in {".jpg", ".jpeg"} and resized.mode in {
            "RGBA",
            "LA",
            "P",
        }:
            resized = resized.convert("RGB")
        resized.save(image_path)


def split_image_to_panel_size(
    image_path: Path,
    dest_folder: Path,
    *,
    start_index: int,
    panel_size: PanelSize,
) -> int:
    try:
        from PIL import Image, ImageOps
    except ImportError as err:
        raise ImportError(
            "Pillow was not found and is needed for panel_size splitting. Is it installed?"
        ) from err

    height, width = panel_size
    if height <= 0 or width <= 0:
        raise ValueError("panel_size must be a tuple of positive integers: (height, width)")

    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        if image.width != width:
            resized_height = max(1, round(image.height * (width / image.width)))
            image = image.resize((width, resized_height), Image.Resampling.LANCZOS)
        else:
            image = image.copy()

        suffix = image_path.suffix.lower() or ".jpg"
        index = start_index
        for top in range(0, image.height, height):
            panel = image.crop((0, top, width, min(top + height, image.height)))
            if panel.height < height:
                padded = Image.new(panel.mode, (width, height), _image_background(panel.mode))
                padded.paste(panel, (0, 0))
                panel = padded

            if suffix in {".jpg", ".jpeg"} and panel.mode in {"RGBA", "LA", "P"}:
                panel = panel.convert("RGB")
            panel.save(dest_folder / f"{index:{FILE_PADDING}}{suffix}")
            index += 1
        return index


def _image_background(mode: str) -> int | tuple[int, ...]:
    if mode == "RGBA":
        return (255, 255, 255, 0)
    if mode == "LA":
        return (255, 0)
    if mode == "L":
        return 255
    return (255, 255, 255)


def async_download_image(data: AsyncDownloadImageInput) -> None:
    """
    Download an image from a URL to a destination folder, fixing the file extension if necessary.

    :param `data`: A tuple of the url, destination folder, filename, and headers.
    """
    url, dest_folder, filename, headers, panel_size = data
    dest_folder = Path(dest_folder)

    name = filename or url.split("/")[-1]
    dest_file = dest_folder / name

    times = 0
    while (res := RealRequests.get(url, headers=headers, timeout=5)).status_code == 429:
        # there is no clean way to raise an error in a pool
        # so we just return early and check it later
        times += 1
        sleep(1)
        if times >= 3:
            return

    with open(dest_file, "wb") as file:
        file.write(res.content)

    # if the file extension is lying
    # rename it so epubcheck doesn't yell at us
    ext = filetype.guess(dest_file)
    if ext is not None and ext.extension in ["jpg", "png", "gif"]:
        renamed_file = dest_file.with_suffix(f".{ext.extension}")
        dest_file.rename(renamed_file)
        dest_file = renamed_file

    if panel_size is not None:
        resize_image_to_panel_size(dest_file, panel_size)


def download_images(
    urls: Sequence[str],
    dest_folder: Path | str,
    *,
    filestems: Sequence[str] | None = None,
    headers: dict[str, str] | None = None,
    threads: int = 1,
    panel_size: PanelSize | None = None,
) -> Iterator[None]:
    """
    Download one or multiple URLs to a destination folder.
    Raises ValueError if the folder does not exist.

    :param `urls`: A list of URLs to download.
    :param `dest_folder`: The path to download files into.
    :param `filestems`: Specify the name of each downloaded file instead of the default.
    :param `headers`: Request headers
    :param `threads`: The number of processes to open
    :param `panel_size`: If provided as `(height, width)`, resize each image to
    fill that exact size and crop the overflow before the download job completes.
    :returns An Iterator that yields `None` for each downloaded file.
    """
    dest_folder = Path(dest_folder)

    # attempt to create
    dest_folder.mkdir(exist_ok=True)

    # args to async_download
    map_pool: list[AsyncDownloadImageInput] = []

    if filestems is None:
        filestems = [f"{i + 1:FILE_PADDING}" for i in range(len(urls))]

    for url, stem in zip(urls, filestems, strict=True):
        _, ext = os.path.splitext(urllib.parse.urlparse(url).path)
        map_pool.append((url, dest_folder, f"{stem}{ext}", headers, panel_size))

    with mp.Pool(threads) as pool:
        yield from pool.imap_unordered(async_download_image, map_pool)


def download_images_as_panels(
    urls: Sequence[str],
    dest_folder: Path | str,
    *,
    headers: dict[str, str] | None = None,
    threads: int = 1,
    panel_size: PanelSize,
) -> Iterator[None]:
    """
    Download images and split each image vertically into fixed-size panels.

    `panel_size` is `(height, width)`. Each source image is resized to the
    target width while preserving aspect ratio, then split from top to bottom.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(dir=dest_folder, prefix=".mandown-panels-") as temp_dir:
        temp_path = Path(temp_dir)
        temp_stems = [f"source-{i + 1:{FILE_PADDING}}" for i in range(len(urls))]

        yield from download_images(
            urls,
            temp_path,
            filestems=temp_stems,
            headers=headers,
            threads=threads,
            panel_size=None,
        )

        next_index = 1
        for stem in temp_stems:
            source_file = next(iter(sorted(temp_path.glob(f"{stem}.*"))), None)
            if source_file is None:
                continue
            next_index = split_image_to_panel_size(
                source_file,
                dest_folder,
                start_index=next_index,
                panel_size=panel_size,
            )


def clear_numbered_images(path: Path | str) -> None:
    path = Path(path)
    for file in path.iterdir():
        if file.is_file() and file.stem == file.stem.rjust(NUM_LEFT_PAD_DIGITS, "0"):
            file.unlink()
        elif file.is_dir() and file.name.startswith(".mandown-"):
            shutil.rmtree(file)


def read_comic(path: Path | str) -> BaseComic:
    """
    Open a comic from a folder path.

    :param `path`: A folder containing `md-metadata.json`
    :returns A comic with metadata and chapter data of that folder
    :raises `FileNotFoundError` if `md-metadata.json` is not found.
    :raises `IOError` if the path does not exist.
    """
    path = Path(path)
    json_path = path / MD_METADATA_FILE

    if not path.exists():
        raise IOError(f"Path {path} does not exist")

    with open(json_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    return BaseComic(
        BaseMetadata(**data["metadata"]),
        [BaseChapter(**c) for c in data["chapters"]],
    )


def parse_comic(path: Path | str, donor_comic: BaseComic | None = None) -> BaseComic:
    """
    Attempt to construct a Mandown comic from a folder from the filesystem (without most metadata).
    This function is non-destructive and does not modify the filesystem. If you want to save the
    comic, call `save_comic` on the returned `BaseComic`.

    :param `path`: A folder containing images
    :param `donor_comic`: A comic to fill metadata from
    :returns A `BaseComic` with metadata and chapter data of that folder
    """
    path = Path(path)

    title = path.stem
    authors: list[str] = []
    url = ""
    genres: list[str] = []
    description = ""
    cover_art = ""
    metadata = BaseMetadata(title, authors, url, genres, description, cover_art)

    chapters = [
        BaseChapter(inode.stem, "", inode.stem)
        for inode in natsorted(path.iterdir(), key=lambda i: i.stem)
        if inode.is_dir()
    ]

    if donor_comic:
        metadata = donor_comic.metadata
        for local, remote in zip(chapters, donor_comic.chapters):
            local.title = remote.title
            local.url = remote.url

    return BaseComic(metadata, chapters)


def save_comic(comic: BaseComic, path: Path | str) -> None:
    """
    Save an `md-metadata.json` from `comic` into `path`. If `path` does not exist, it will
    be created. THIS FUNCTION IS DESTRUCTIVE AND WILL OVERWRITE ANY EXISTING `md-metadata.json`.

    :param `comic`: A comic to save
    :param `path`: A folder to save `md-metadata.json` into
    """
    path = Path(path)
    path.mkdir(exist_ok=True)

    json_path = path / MD_METADATA_FILE

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(comic.asdict(), file)


def discover_local_images(path: Path | str) -> dict[str, list[Path]]:
    """
    Given a comic path, return a dictionary of chapter_slugs OR cover: images.
    Basically a slightly modified version of os.walk. Only recurses one level deep.

    :param `path`: A folder containing images
    :returns A dictionary of {slugs: images}
    """
    path = Path(path)

    return {
        chap.stem: sorted(chap.iterdir())
        for chap in sorted(path.iterdir())  # iterdir does not guarantee any order
        if chap.is_dir()
    } | {"cover": [cover for cover in path.iterdir() if cover.is_file() and cover.stem == "cover"]}

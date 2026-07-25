# Python API guide

This guide covers Mandown's main public Python API. It focuses on the usual
workflow: find a series, load it as a comic, download it, and optionally process
or convert the downloaded files.

```python
import mandown
```

## Quick start

If you already have a supported series URL:

```python
comic = mandown.query(
    "https://www.webtoons.com/en/action/omniscient-reader/list?title_no=2154"
)

mandown.download(comic, "./downloads")
mandown.convert(
    "./downloads/omniscient-reader",
    mandown.ConvertFormats.CBZ,
    "./books",
)
```

If you only know the series name:

```python
results = mandown.search("solo leveling")
mangadex_matches = results["mangadex"]

if mangadex_matches:
    comic = mangadex_matches[0].comic
    mandown.download(comic, "./downloads")
```

## Searching and querying

### `mandown.search(title)`

Searches Naver Webtoon, WEBTOON, and MangaDex.

Arguments:

- `title` (`str`): A series name or search phrase. Surrounding whitespace is
  removed. An empty search raises `ValueError`.

Returns:

- `SearchResults`: A dictionary-like object with the keys `naver`, `webtoons`,
  and `mangadex`.
- Each key contains an ordered `list[SearchItem]`, or `None` when that site has
  no matches.

Each `SearchItem` provides:

- `source`: The source key.
- `title`: The title shown by the source.
- `url`: A supported series URL.
- `authors`: Authors available in the search response.
- `cover_art`: Cover URL when available.
- `comic`: A lazily loaded `BaseComic`. The network request happens on first
  access and the resulting object is cached.
- `asdict()`: Converts the lightweight result to a plain dictionary.

`SearchResults.asdict()` converts all results to plain dictionaries. This is
useful before serializing them as JSON.

```python
import json
import mandown

results = mandown.search("tower of god")

with open("search-results.json", "w", encoding="utf-8") as file:
    json.dump(results.asdict(), file, ensure_ascii=False, indent=2)

webtoons_matches = results["webtoons"]
if webtoons_matches is not None:
    first_match = webtoons_matches[0]
    comic = first_match.comic
```

Search results are deliberately lightweight. Calling `search()` does not fetch
the metadata and chapter list for every match.

### `mandown.query(url)`

Loads one supported series URL.

Arguments:

- `url` (`str`): A series or chapter URL recognized by one of Mandown's source
  adapters.

Returns:

- `BaseComic`: The full comic metadata and chapter list.

Raises:

- `ValueError`: The URL does not match a supported source.
- Network or source-specific errors if the remote site cannot be queried.

```python
comic = mandown.query("https://mangadex.org/title/SERIES_ID")

print(comic.metadata.title)
print(comic.metadata.authors)
print(comic.chapters[0].title)
```

## Core objects

### `BaseComic`

A queried or locally loaded comic.

Important members:

- `metadata` (`BaseMetadata`): Title, authors, source URL, genres, description,
  cover URL, and generated `title_slug`.
- `chapters` (`list[BaseChapter]`): Chapters in download order.
- `source`: The adapter used for chapter image requests.
- `asdict()`: Returns metadata and chapters as plain dictionaries.
- `get_chapter_image_urls(chapter)`: Fetches the image URLs for one chapter.
- `set_chapter_range(...)`: Keeps only a selected chapter range.
- `update(chapters=True, metadata=True)`: Refreshes remote chapter and metadata
  information.

### `BaseMetadata`

Fields:

- `title` (`str`)
- `authors` (`list[str]`)
- `url` (`str`)
- `genres` (`list[str]`)
- `description` (`str`)
- `cover_art` (`str`)
- `title_slug` (`str`, generated from the title)

`metadata.asdict()` returns the serializable metadata fields.

### `BaseChapter`

Fields:

- `title` (`str`)
- `url` (`str`)
- `slug` (`str`)
- `chapter_number` (`str`)

`chapter.numeric_chapter_number` is a `Decimal` when `chapter_number` is
numeric, otherwise `None`.

## Downloading

### `mandown.download(comic, path=".", *, ...)`

Downloads a comic and waits until the download thread finishes.

Arguments:

- `comic` (`BaseComic | str`): A queried comic or supported URL.
- `path` (`Path | str`): Parent destination directory. Mandown creates a child
  folder using `comic.metadata.title_slug`.
- `start` (`int | None`): First selected chapter.
- `end` (`int | None`): End of the selected range.
- `threads` (`int`): Parallel image download workers. Default: `4`.
- `only_download_missing` (`bool`): Skip existing numbered images. Default:
  `True`.
- `raise_on_failed_download` (`bool`): Raise `ImageDownloadError` when the
  downloaded image count is incomplete. Default: `True`.
- `panel_size` (`tuple[int, int] | None`): `(height, width)` used to resize and
  split long images into fixed panels. The `download()` default is
  `(1280, 800)`. Pass `None` to preserve normal source pages.
- `image_format` (`str | None`): Convert images to `"jpg"` or `"png"`.
- `progress_callback` (`Callable[[Progress, Progress], None] | None`): Called
  with overall and current-chapter progress objects.

Chapter range semantics:

- When a source exposes real chapter numbers, `start` and `end` refer to those
  numbers and both are inclusive.
- Otherwise they are list indexes: `start` is inclusive and `end` is exclusive.

Returns:

- `(main_progress, chapter_progress, thread)`: Two `Progress` objects and the
  completed download thread.

```python
def show_progress(overall, chapter):
    print(f"series: {overall.progress}% chapter: {chapter.progress}%")


overall, chapter, thread = mandown.download(
    comic,
    "./downloads",
    threads=8,
    panel_size=None,
    image_format="jpg",
    progress_callback=show_progress,
)

assert not thread.is_alive()
```

### `mandown.download_progress(comic, path=".", *, ...)`

The iterator form of the downloader.

It accepts the same main download options, plus optional `main_progress`,
`chapter_progress`, and a no-argument `progress_callback` for integrations that
manage their own `Progress` instances.

Returns:

- `Iterator[str]`: Yields each chapter title as that chapter begins.

```python
for chapter_title in mandown.download_progress(comic, "./downloads"):
    print("Downloading:", chapter_title)
```

## Local metadata

### `mandown.save_metadata(comic, path)`

Writes the comic to `<path>/md-metadata.json`.

Arguments:

- `comic` (`BaseComic`): Comic data to save.
- `path` (`Path | str`): Existing destination comic folder.

Returns:

- `None`

### `mandown.load(path)`

Loads a Mandown comic folder containing `md-metadata.json`.

Arguments:

- `path` (`Path | str`): Mandown comic folder.

Returns:

- `BaseComic`

Raises:

- `FileNotFoundError`: `md-metadata.json` does not exist.
- `OSError`: The path cannot be read.

```python
mandown.save_metadata(comic, "./downloads/my-series")
local_comic = mandown.load("./downloads/my-series")
```

### `mandown.init_parse_comic(path, donor_comic=None, download_cover=False)`

Loads an existing Mandown folder or initializes metadata for a folder of comic
images.

Arguments:

- `path` (`Path | str`): Comic folder.
- `donor_comic` (`BaseComic | None`): Optional source comic used to fill
  metadata.
- `download_cover` (`bool`): Download the donor's cover when initializing.
  Requires `donor_comic`.

Returns:

- `BaseComic`: Existing or newly parsed local comic.

Raises:

- `AttributeError`: `download_cover=True` was used without `donor_comic`.

## Converting

### `mandown.convert(comic_path, to, dest_folder=None, remove_after=False)`

Converts a Mandown image folder or an existing comic archive.

Arguments:

- `comic_path` (`Path | str`): Mandown folder or supported input archive.
- `to` (`ConvertFormats`): `CBZ`, `EPUB`, `PDF`, `MOBI`, or `NONE`.
- `dest_folder` (`Path | str | None`): Output directory. Defaults to the
  current directory.
- `remove_after` (`bool`): Remove the input after successful conversion.
  Default: `False`.

Returns:

- `None`

```python
mandown.convert(
    "./downloads/my-series",
    mandown.ConvertFormats.EPUB,
    "./books",
)
```

### `mandown.convert_progress(...)`

Iterator form of conversion. It also accepts `split_by_chapters=True` to create
one output per chapter for a Mandown folder.

Returns:

- `Iterator[str | int]`: Conversion progress events produced by the conversion
  backend.

## Image processing

### `mandown.process(comic_path, ops, config=None)`

Processes images found below a comic directory.

Arguments:

- `comic_path` (`Path | str`): Folder containing chapter image folders.
- `ops` (`list[ProcessOps]`): Operations applied in order.
- `config` (`ProcessConfig | None`): Resize configuration when required.

Returns:

- `None`

Available operations include:

- `ProcessOps.ROTATE_DOUBLE_PAGES`
- `ProcessOps.SPLIT_DOUBLE_PAGES`
- `ProcessOps.TRIM_BORDERS`
- `ProcessOps.RESIZE`
- `ProcessOps.WEBP_TO_PNG`
- `ProcessOps.NO_POSTPROCESSING`

```python
config = mandown.ProcessConfig(target_size=(1200, 800))

mandown.process(
    "./downloads/my-series",
    [mandown.ProcessOps.TRIM_BORDERS, mandown.ProcessOps.RESIZE],
    config,
)
```

### `mandown.process_progress(...)`

Iterator form of image processing.

Returns:

- `Iterator[str]`: Yields `"Processing"` after each processed image.

## Error handling

Remote sites can change or become unavailable. Code that accepts user input
should normally handle unsupported URLs, invalid search text, and request
errors:

```python
import requests

try:
    results = mandown.search(user_text)
except ValueError as error:
    print("Invalid search:", error)
except requests.RequestException as error:
    print("A source could not be reached:", error)
```

Source adapters can also raise source-specific `RuntimeError` values when a
remote response cannot be used.

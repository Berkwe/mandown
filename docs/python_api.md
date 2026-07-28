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
import asyncio


async def find_source():
    async with mandown.AniListClient() as client:
        results = await client.search_manga(
            "solo leveling",
            include_external_links=True,
        )
        for manga in results.items:
            if isinstance(manga, mandown.AniListManga):
                sources = client.extract_supported_sources(manga.external_links)
                if sources:
                    return sources[0].url


source_url = asyncio.run(find_source())
if source_url:
    mandown.download(source_url, "./downloads")
```

## AniList search and querying

AniList is Mandown's only active title-search and metadata source. The primary
API is async and keeps one HTTP session for the lifetime of the client:

```python
import asyncio
from mandown import AniListClient


async def main():
    async with AniListClient() as client:
        results = await client.search_manga("Solo Leveling", per_page=10)
        selected = results.items[0]
        details = await client.get_manga(selected.id)
        sources = client.extract_supported_sources(details.external_links)
        print(selected.title.english, sources)


asyncio.run(main())
```

### `AniListClient(...)`

```python
AniListClient(
    *,
    endpoint="https://graphql.anilist.co",
    timeout=(5, 20),
    session=None,
)
```

- `endpoint` (`str`): Configurable GraphQL endpoint.
- `timeout` (`float | tuple[float, float]`): `requests` timeout passed to every
  GraphQL POST.
- `session` (`requests.Session | None`): Optional externally managed session.
  When omitted, the client creates and owns one reusable session.

The client is an async context manager. `aclose()` closes only a session created
by the client; an injected session remains owned by the caller.

Public configuration constants:

- `ANILIST_ENDPOINT`: Default GraphQL endpoint.
- `ANILIST_MAX_PER_PAGE = 50`: AniList root `Page.perPage` API maximum.
- `DEFAULT_PER_PAGE = 10`: Python API and CLI default.
- `MANDOWN_PER_PAGE_LIMIT = 25`: Mandown CLI product/performance limit.

### `search_manga(...)`

`search_manga()` uses `Page.media` with `type: MANGA`, preserves AniList's
result order, and returns `AniListSearchResponse(items, page_info)`.
`page_info` exposes `current_page`, `last_page`, `has_next_page`,
`per_page`, and `total`. AniList notes that `total` and `lastPage` are not
reliable for pagination logic; use `has_next_page`.

Arguments:

- `query` (`str`): General AniList search phrase. Empty values raise
  `ValueError`.
- `page` (`int`): One-based page number. Default: `1`.
- `per_page` (`int`): Results per page. Default: `10`; AniList API range:
  `1..50`.
- `fields` (`AniListFieldSet | None`): Exact trusted field preset/selection.
  Search defaults to `CARD`; details default to `DETAIL`.
- `include_details`: Upgrade a search to detailed fields in one request.
- `include_external_links`, `include_description`, `include_cover`:
  Explicitly add or remove those fields. `None` preserves the preset.

Available presets are `LIGHT`, `CARD`, `DETAIL`, and `FULL`. Query text is
built only from supported field fragments. User input and pagination values are
always sent as GraphQL variables.

Field presets:

| Preset | Requested media fields |
| --- | --- |
| `LIGHT` | `id`, titles, `siteUrl` |
| `CARD` | `LIGHT` plus MAL ID, synonyms, format, status, country, popularity, score, and cover |
| `DETAIL` | `CARD` plus description, chapters, volumes, genres, and external links |
| `FULL` | Every AniList field currently supported by Mandown |

`id` is always requested even for a custom `AniListFieldSet`. Custom sets accept
only `AniListField` values; arbitrary GraphQL text is rejected.

A one-request rich search is also supported:

```python
async with AniListClient() as client:
    results = await client.search_manga(
        "Solo Leveling",
        include_details=True,
        include_external_links=True,
        include_description=True,
    )
```

### `get_manga(...)`

```python
details = await client.get_manga(
    105398,
    fields=mandown.AniListFieldSet.DETAIL,
    include_external_links=True,
    include_description=True,
    include_cover=True,
)
```

`get_manga()` requires a positive AniList ID and returns `AniListManga`.
`DETAIL` is the default field set. The three include options accept `True`,
`False`, or `None`; `None` preserves the selected preset.

### Result models

Returned models include `AniListTitle`, `AniListCoverImage`,
`AniListExternalLink`, `AniListMangaSummary`, `AniListManga`,
`AniListPageInfo`, and `AniListSearchResponse`. Nullable English titles,
descriptions, chapter/volume counts, and empty external-link lists remain
nullable/empty instead of being replaced with invented values. Unknown AniList
enum strings are retained without failing parsing.

- `AniListMangaSummary`: `id`, `id_mal`, `title`, `synonyms`, `format`,
  `status`, `country_of_origin`, `popularity`, `average_score`, `cover_image`,
  and `site_url`.
- `AniListManga`: all summary fields plus `description`, `chapters`, `volumes`,
  `genres`, and `external_links`.
- `AniListTitle`: nullable `romaji`, `english`, and `native`.
- `AniListCoverImage`: nullable `extra_large`, `large`, `medium`, and `color`.
- `AniListExternalLink`: `site`, `url`, `type`, `language`, `is_disabled`, and
  `notes`.
- `AniListSearchResponse`: ordered `items` tuple and `page_info`.
- `AniListPageInfo`: `current_page`, `last_page`, `has_next_page`, `per_page`,
  and `total`. Use `has_next_page` for pagination decisions.

### Supported download sources

`extract_supported_sources()` accepts AniList external links and returns only
resolver-ready MangaDex, WEBTOON, Naver Webtoon, and Naver Series URLs. It
validates both an explicit hostname allowlist and Mandown's URL resolver; a
plausible `site` label cannot make an unsupported domain valid. Disabled links
are excluded by default, duplicate provider/language/URL entries are removed,
and `STREAMING` links sort before `INFO` links.

Each `AniListSupportedSource` contains:

- `provider`: `mangadex`, `webtoons`, `naver`, or `naver_series`.
- `url`: URL accepted by the matching Mandown resolver.
- `language`, `site`, and `type`: AniList external-link metadata.
- `is_disabled`: Whether AniList marked the link disabled.

Pass `include_disabled=True` only when disabled links are intentionally needed:

```python
sources = mandown.extract_supported_sources(
    details.external_links,
    include_disabled=True,
)
```

Unsupported links remain available in `AniListManga.external_links`; they are
not passed to `mandown.query()`.

### Errors and lifecycle

The client maps timeout, network, HTTP, invalid JSON/shape, GraphQL, and HTTP 429
responses to distinct `AniListError` subclasses. `AniListRateLimitError`
retains `retry_after` and `reset_at` when AniList supplies those headers.
Use `async with` or call `await client.aclose()` when finished.

| Exception | Meaning |
| --- | --- |
| `AniListTimeoutError` | Connect/read timeout |
| `AniListNetworkError` | Connection or other request failure |
| `AniListHTTPError` | Non-success HTTP status |
| `AniListRateLimitError` | HTTP 429; includes `retry_after` and `reset_at` |
| `AniListResponseError` | Invalid JSON or unexpected response shape |
| `AniListGraphQLResponseError` | GraphQL `errors`; typed entries are available in `graphql_errors` |

All inherit from `AniListError`, which inherits from `MandownError`.

### Search limitations

AniList exposes a general search index. It does not offer a separate
English-title substring search. A short fragment such as `"Omni"` is therefore
not guaranteed to return a title whose English name contains that fragment.
Mandown does not build a local title index.

### Deprecated compatibility APIs

`mandown.search(title, source=None)` remains as a synchronous migration adapter.
It returns the historical `SearchResults` mapping with `naver`, `webtoons`,
`mangadex`, and `anilist` keys, but only `anilist` can contain matches.
The adapter requests external links so `SearchItem.comic` can lazily resolve a
supported downloader URL. Retired source filters raise a migration error.

`mandown.search_all()` remains a deprecated async generator and yields one
`("anilist", matches)` batch. Multi-provider fallback, retry, and deduplication
options are retired and reject non-default use.

The CLI equivalent is:

```console
mandown search "Solo Leveling" --page 1 --limit 10
mandown search "Solo Leveling" --details --external-links
```

The AniList API accepts up to 50 root `Page` results. Mandown's CLI deliberately
limits `--limit` to `1..25` as a product/performance choice; this is not an
AniList API limitation.

CLI options:

- `--page`: One-based AniList page. Default: `1`.
- `--limit`: Results per page. Default: `10`; Mandown CLI range: `1..25`.
- `--details`: Show description, chapter/volume counts, genres, and links.
- `--external-links`: Show all AniList links and resolver-ready sources.

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

AniList search failures use the typed `AniListError` hierarchy described above.
Direct URL resolver/downloader implementations can still raise
`SourceResponseError`, `requests.RequestException`, or source-specific runtime
errors when a remote site changes or becomes unavailable.

```python
async def safe_search(user_text):
    try:
        async with mandown.AniListClient() as client:
            return await client.search_manga(user_text)
    except ValueError as error:
        print("Invalid search:", error)
    except mandown.AniListRateLimitError as error:
        print("Rate limited; retry after:", error.retry_after)
    except mandown.AniListError as error:
        print("AniList search failed:", error)
```

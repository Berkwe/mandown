The library supports a few more features that the CLI does not, including being able to work with raw `BaseComic` and `BaseMetadata` objects, querying sources directly, or perform operations on Mandown comics stored in the file system.

## Querying

If you want to do whatever you want with Mandown's sources, you can use the `mandown.query` function. This function returns a `BaseComic`, which contains two fields: `metadata` and `chapters`. The former is a `BaseMetadata` object, and the latter is a list of `BaseChapter` objects.

```python
import mandown

comic: BaseComic = mandown.query("https://example.com/comic")

print(comic.metadata.title, comic.chapters[0].title)
```

## Searching by series name

Search Naver Webtoon, WEBTOON and MangaDex at once:

```python
results = mandown.search("solo leveling")
webtoons_results = results["webtoons"]

if webtoons_results is not None:
    match = webtoons_results[0]
    print(match.title, match.url)
    comic = match.comic
```

For concurrent search across Naver, MangaDex, and AniList, with supported
Naver, WEBTOON, and MangaDex URLs from AniList merged into their source batch:

```python
import asyncio


async def search_concurrently():
    async for source, matches in mandown.search_all("solo leveling"):
        print(source, matches)


asyncio.run(search_concurrently())
```

Naver and MangaDex batches that finish before AniList wait so they can include
its matching URLs. The standalone WEBTOON search is disabled in this
orchestrator by default. To use it only when AniList contains no WEBTOON URL,
call `mandown.search_all("solo leveling", webtoons_fallback=True)`.

The synchronous `search()` mapping uses a list of lightweight `SearchItem`
objects or `None` for each site. Every `search_all()` batch instead contains a
list, which can be empty. Accessing `match.comic` performs the same full query
as `mandown.query(match.url)`; catalog-only URLs unsupported by a Mandown
adapter raise `ValueError`. Search data can be serialized with
`results.asdict()` or `match.asdict()`.

The synchronous default keeps AniList as `None`; use `source="anilist"` for
only AniList, or `search_all()` for concurrent search with AniList-derived
source results.

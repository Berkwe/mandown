The library supports a few more features that the CLI does not, including being able to work with raw `BaseComic` and `BaseMetadata` objects, querying sources directly, or perform operations on Mandown comics stored in the file system.

## Querying

If you want to do whatever you want with Mandown's sources, you can use the `mandown.query` function. This function returns a `BaseComic`, which contains two fields: `metadata` and `chapters`. The former is a `BaseMetadata` object, and the latter is a list of `BaseChapter` objects.

```python
import mandown

comic: BaseComic = mandown.query("https://example.com/comic")

print(comic.metadata.title, comic.chapters[0].title)
```

## Searching by series name

AniList is the active title-search and metadata source. Search lightweight cards,
then fetch details only for the selected entry:

```python
import asyncio
import mandown


async def find_comic():
    async with mandown.AniListClient() as client:
        results = await client.search_manga("solo leveling", per_page=10)
        details = await client.get_manga(results.items[0].id)
        sources = client.extract_supported_sources(details.external_links)
        if sources:
            return mandown.query(sources[0].url)


comic = asyncio.run(find_comic())
```

Use `include_details=True` for a single rich search request. The default page
size is 10. The Python client accepts AniList's root `Page` range of 1 through
50; the `mandown search` CLI intentionally caps `--limit` at 25.

For one-request rich results:

```python
async with mandown.AniListClient() as client:
    results = await client.search_manga(
        "Solo Leveling",
        page=1,
        per_page=10,
        include_details=True,
        include_external_links=True,
        include_description=True,
    )
```

Use `AniListFieldSet.LIGHT`, `.CARD`, `.DETAIL`, or `.FULL` when the caller
wants an explicit field contract. Include flags can add or remove description,
cover, and external-link fields without placing user input in GraphQL query
text.

AniList uses one general title index and does not expose an English-title-only
substring filter. Unsupported external links remain metadata and are never sent
to Mandown's URL resolvers.

The old synchronous `mandown.search()` dictionary and async
`mandown.search_all()` generator remain deprecated migration adapters. They
query only AniList; Naver, WEBTOON, and MangaDex native text-search providers
are archived and no longer run.

Direct URLs are unaffected by this migration:

```python
comic = mandown.query(
    "https://www.webtoons.com/en/action/omniscient-reader/list?title_no=2154"
)
mandown.download(comic, "./downloads")
```

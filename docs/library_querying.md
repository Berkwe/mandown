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

Each site maps to a list of lightweight `SearchItem` objects, or `None` when
that site has no matches. Accessing `match.comic` performs the same full query
as `mandown.query(match.url)`. Search data can be serialized with
`results.asdict()` or `match.asdict()`.

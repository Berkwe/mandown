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
remain archived, except MangaDex candidate search in the selected-ID flow below.

Direct URLs are unaffected by this migration:

```python
comic = mandown.query(
    "https://www.webtoons.com/en/action/omniscient-reader/list?title_no=2154"
)
mandown.download(comic, "./downloads")
```

## Two-stage source discovery

The first search lists AniList cards only. After the user explicitly chooses a
card, resolve that exact ID rather than searching its title in AniList again:

```python
import asyncio
import mandown

async def main():
    async with mandown.AniListClient() as client:
        cards = await client.search_manga("GOSU")
    # In a UI, take this ID from the card the user clicks.
    selected_id = cards.items[0].id
    result = await mandown.search_sources(selected_id, threshold=54.0)
    for link in result.links:
        print(link.provider, link.language, link.url, link.method, link.metadata_status)
    print(result.item.urls)
    print(result.events)

asyncio.run(main())
```

Active supported AniList links are trusted without provider search, metadata
hydration or JEV. Naver (including Naver Series) and WEBTOON are **external-only**:
if AniList has no supported active link, their status is `SKIPPED_EXTERNAL_ONLY`.
MangaDex is currently the only missing catalog searched by title.

English and romaji titles are normalized/deduplicated and searched together.
If they yield no verified identifier/JEV match, the remaining native/alternative
titles are searched with at most six concurrent requests (12 distinct titles
total). An arbitrary search hit does not stop fallback. Candidates retain the
tested provider-ID/URL deduplication and title/creator/year/relation ranking.
Conflicting AniList/MAL IDs are rejected; exact identity bypasses both JEV and
metadata hydration.

`candidate_limit=3` applies **per phase**, allowing up to three primary and
three new fallback JEV candidates. An already evaluated candidate is never
retried in fallback. Metadata/JEV pipelines run up to three at a time per
provider; all providers share a six-worker request pool. The highest passing
score wins within a phase (rank breaks ties); a verified primary result skips
fallback. Provider coordination uses separate threads, so future `search_missing`
providers can run concurrently without blocking the shared request workers.

The central `mandown.source_search.SOURCE_POLICIES` registry holds a
`ProviderPolicy(mode, search)` for each provider. New searchable providers add a
search callable returning `SourceSearchResult`; external-only providers omit it.
Provider-specific identity/URL extraction and AniList supported-source recognition
must also be registered when introducing a new domain.

The production default is **54%**, calibrated from the AniList-anchored
evaluation run of 28 September 2026. Its 21 labeled JEV pairs
contain 8 positives and 13 negatives, with no observed classification errors at
54%; the empirical perfect interval was 33–75%. This is a sample-based
calibration, not a guarantee for every title. The report's runtime setting of
45% and defaults of older experimental commands are not the production default.
The prompt SHA-256 is
`a66c3474bc2e319537e2e2deef88dc93ce403be24ab915d4f05bc0312825404b`.
The installed package needs no local results directory.

Python `logging` emits decisions at INFO level. `SAME_IDENTIFIER` always
includes matching identity evidence and the message
**“JEV çağrısı atlandı: kesin kimlik eşleşmesi”**.
Errors are also retained in `result.errors`; one failed source does not discard
other sources. Direct and exact-ID links have `metadata_status="not_fetched"`:
this is intentional, not an availability error. Only uncertain JEV candidates
need source metadata. No chapter lists or chapter images are fetched by search;
normal query/download behavior remains separate.

### Credentials and CLI

Configure `JEVMODEL_API_KEY` in the environment. Optional failover keys are
`JEVMODEL_API_KEY_FALLBACK` and `JEVMODEL_API_KEY_FALLBACK_2` through `_9`.
Credentials remain outside the package and are redacted from diagnostics.

```bash
mandown search "GOSU"
mandown sources 86640 --threshold 54 --candidate-limit 3
mandown sources 86640 --json
```

`mandown search` lists AniList cards; `mandown sources` resolves the selected ID.
The Python equivalent is `await mandown.search_sources(86640, threshold=54.0)`.
No chapter lists or images are fetched during discovery.

Redacted timing/decision JSONL rows are appended to `search_logs/search.jsonl`.
Set `MANDOWN_SEARCH_LOG` to select another location. `result.timings` exposes
AniList detail, resolution, total and provider-phase durations.

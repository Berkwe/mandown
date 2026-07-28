# Legacy provider text search

This directory preserves the former native title-search implementations for
Naver Webtoon, WEBTOON, and MangaDex. They are retained for history and
migration reference only.

These modules:

- are not imported by `mandown.search`, `AniListClient`, or the source registry;
- are not exported from the `mandown` package;
- must not be used as a fallback in production search;
- do not replace or disable the active URL resolver/downloader classes in
  `mandown.sources`.

AniList is the only active metadata and title-search source. Use
`AniListClient.search_manga()` and pass supported external links to
`mandown.query()` when downloadable comic data is needed.

# mandown

![Supported Python versions](https://img.shields.io/pypi/pyversions/mandown)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![Download from PyPI](https://img.shields.io/pypi/v/mandown)](https://pypi.org/project/mandown)
[![Download from the AUR](https://img.shields.io/aur/version/mandown-git)](https://aur.archlinux.org/packages/mandown-git)
[![Latest release](https://img.shields.io/github/v/release/potatoeggy/mandown?display_name=tag)](https://github.com/potatoeggy/mandown/releases/latest)
[![License](https://img.shields.io/github/license/potatoeggy/mandown)](/LICENSE)

Mandown is a comic downloader and a CBZ, EPUB, MOBI, and/or PDF converter. It also supports image post-processing to make them more readable on certain devices similarly to [Kindle Comic Converter](https://github.com/ciromattia/kcc).

## Features

- Download comics from [supported sites](#supported-sites)
  - Supports downloading a range of chapters
  - Supports multithreaded downloading
- Search manga metadata through AniList
  - Lightweight cards, paginated results, and on-demand details
  - Resolver-ready MangaDex, WEBTOON, and Naver external links
- Process downloaded images
  - Rotate or split double-page spreads
  - Trim borders
  - Resize images
- Convert downloaded comics to CBZ, EPUB, MOBI, or PDF
  - Convert any other CBZ, EPUB, MOBI, or PDF comic to CBZ, EPUB, MOBI, or PDF
- [A library to easily do all of this from other Python scripts](#basic-library-usage)

## Usage

Run `mandown --help` or see the [docs](/docs/) for more information and examples.

```
mandown get <URL>
```

Search AniList when you do not already know a supported series URL:

```
mandown search "Solo Leveling"
mandown search "Solo Leveling" --details --external-links
```

The CLI returns 10 results by default and accepts `--page` and `--limit`.
Mandown caps the CLI limit at 25 as a performance choice; the Python client can
use AniList's full `Page.perPage` range through 50.

To convert the downloaded contents to CBZ/EPUB/MOBI/PDF, append the `--convert` option. To apply image processing to the downloaded images, append the `--process` option.

```
mandown get <URL> --convert epub --process rotate_double_pages
```

To download only a certain range of chapters, append the `--start` and/or `--end` options.

> **Note:** `--start` and `--end` are _inclusive_, i.e., using `--start 2 --end 3` will download chapters 2 and 3.

To convert an existing folder or comic file without downloading anything (like a stripped-down version of <https://github.com/ciromattia/kcc>), use the `convert` command.

```
mandown convert <FORMAT> <PATH_TO_COMIC>
```

To process an existing folder without downloading anything, use the `process` command.

```
mandown process <PROCESS_OPERATIONS> <PATH_TO_FOLDER>
```

Where `PROCESS_OPERATIONS` is an option found from running `mandown process --help`.

## Installation

Install the package from PyPI:

```
pip3 install git+https://github.com/Berkwe/mandown
```

Install the optional large dependencies for some features of Mandown:

```
# graphical interface (GUI)
pip3 install PySide6
```

Arch Linux users may also install the package from the [AUR](https://aur.archlinux.org/packages/mandown-git):

```
git clone https://aur.archlinux.org/mandown-git.git
makepkg -si
```

Or, to build from source:

Mandown uses [poetry](https://github.com/python-poetry/poetry) for dependency management.

```
git clone https://github.com/Berkwe/mandown
poetry install
poetry build
pip3 install dist/mandown*.whl
```

## Supported sites

To request a new site, please file a [new issue](https://github.com/potatoeggy/mandown/issues/new?title=Source%20request:).

- <https://bato.to>
- <https://comicfury.com>
- https://\*.thecomicseries.com
- <https://natomanga.com>
- <https://webtoons.com>
- <https://comic.naver.com>
- <https://series.naver.com> (when the comic has a matching Naver Webtoon edition)
- <https://mangadex.org>
- <https://readcomiconline.li>
- <https://www.kuaikanmanhua.com>

## Basic library usage

See the [Python API guide](/docs/python_api.md) for the main functions,
arguments, return values, and examples. The [topic guides](/docs/) contain
more information about downloading, processing, and conversion.

Search AniList, load details only for the selected result, and pass a supported
external URL to Mandown's existing resolver:

```python
import asyncio
import mandown


async def find_download_url():
    async with mandown.AniListClient() as client:
        results = await client.search_manga("Solo Leveling")
        details = await client.get_manga(results.items[0].id)
        sources = client.extract_supported_sources(details.external_links)
        return sources[0].url if sources else None


download_url = asyncio.run(find_download_url())
if download_url:
    mandown.download(download_url, "./downloads")
```

AniList supplies the first-stage title list. After selecting an ID,
`await mandown.search_sources(id, threshold=54.0)` discovers and merges
missing MangaDex sources using identifiers and JEV. WEBTOON and Naver links
are accepted only from AniList external links, without additional requests.
Direct URL querying and
downloading for MangaDex, WEBTOON, Naver Webtoon, and the other supported sites
continues to work normally.

To just download the images:

```python
import mandown

mandown.download("https://comic-site.com/the-best-comic")
```

To download and convert to EPUB:

```python
import mandown

comic = mandown.query("https://comic-site.com/the-best-comic")
mandown.download(comic)
mandown.convert(comic, title=comic.metadata.title, to="epub")
```

### Discover sources for a selected manga

```bash
mandown search "Solo Leveling"
mandown sources 105398
mandown sources 105398 --threshold 54 --candidate-limit 3 --json
```

The second command trusts AniList external links and searches MangaDex only when
its link is missing. Naver/WEBTOON are external-link-only. Set
`JEVMODEL_API_KEY` (and optional fallback keys) in the environment for uncertain
JEV candidates; direct links and exact identity matches need no key.
See [the source discovery API](docs/python_api.md#search_sourcesanilist_id--threshold540-candidate_limit3).

### Persistent Jev comparison cache

Source discovery reuses successful Jev scores in a local SQLite cache (30-day
TTL), including negative matches. Identical inputs need no Jev key on a cache hit;
new or expired comparisons still need one. Metadata lookup and source searches
still run, so cached identity scores do not freeze chapter availability.

- Default: `$XDG_CACHE_HOME/mandown/jev.sqlite3` or `~/.cache/mandown/jev.sqlite3`.
- `MANDOWN_JEV_CACHE=/path/to/jev.sqlite3` selects a persistent database.
- `MANDOWN_JEV_CACHE=off` disables caching.
- `MANDOWN_JEV_CACHE_REVISION=2` invalidates previous scores after an upstream
  `jev-latest` model update. The provider alias does not expose model revisions.

The cache fingerprint covers the exact normalized comparison records, prompt,
model, endpoint and cache revision, not the threshold or API key. Changing the
threshold reevaluates the saved probability without another API call. Cache
failures fall back to ordinary comparison; API failures are never saved.
Identical concurrent requests are coalesced within one Python process; independent
processes have atomic SQLite writes but may both compare a simultaneous miss.

Share explicitly reviewed score snapshots, not the runtime database or logs:

```python
from mandown import JevCache

cache = JevCache("/path/to/jev.sqlite3")
cache.export_seed("jev-seed.json")
# Another installation, before search_sources():
JevCache("/other/path/jev.sqlite3").import_seed("jev-seed.json")
```

Seeds contain only fingerprints, probabilities, model names and original
creation timestamps. Import validates the entire snapshot before writing,
preserves newer local entries and skips expired scores. Seed import is explicit;
no remote snapshot is downloaded automatically. A shared seed removes the key
requirement only for matching, unexpired inputs. Preserve the upstream license
when distributing library changes.

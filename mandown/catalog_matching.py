"""Catalog candidate retrieval shared by production and AniList experiments."""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .anilist import AniListClient, AniListManga
from .search_common import parse_mangadex_id, parse_naver_id, parse_webtoons_id
from .sources.base_source import SourceSearchResult


def _search_mangadex(query: str) -> list[SourceSearchResult]:
    from .catalog_search import mangadex

    return mangadex.search(query)


def _search_naver(query: str) -> list[SourceSearchResult]:
    from .legacy.search import naver

    return naver.search(query)


def _search_webtoons(query: str) -> list[SourceSearchResult]:
    from .legacy.search import webtoons

    return webtoons.search(query)


PROVIDERS: dict[str, Callable[[str], list[SourceSearchResult]]] = {
    "mangadex": _search_mangadex,
    "naver": _search_naver,
    "webtoons": _search_webtoons,
}

PROVIDER_ID_KEYS = {
    "mangadex": "mangadex_id",
    "naver": "naver_id",
    "webtoons": "webtoons_id",
}


RELATION_MARKERS = (
    "pre serialization",
    "preserialization",
    "pilot",
    "prototype",
    "spin off",
    "side story",
    "gaiden",
    "oejeon",
    "sequel",
    "prequel",
    "doujinshi",
    "fancomic",
    "parody",
)


CREATOR_ROLE_MARKERS = ("story", "art", "original", "creator", "character design")


ALIAS_LIMIT = 12


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def title_aliases(manga: AniListManga, limit: int = ALIAS_LIMIT) -> list[str]:
    ordered = [manga.title.english, manga.title.romaji, manga.title.native, *manga.synonyms]
    aliases: list[str] = []
    seen: set[str] = set()
    for value in ordered:
        if not value or not (key := normalize_text(value)) or key in seen:
            continue
        seen.add(key)
        aliases.append(value.strip())
        if len(aliases) == limit:
            break
    return aliases


def creator_names(manga: AniListManga) -> tuple[list[str], list[dict[str, Any]]]:
    creators = [
        creator
        for creator in manga.creators
        if creator.role
        and any(marker in creator.role.casefold() for marker in CREATOR_ROLE_MARKERS)
    ]
    primary = list(
        dict.fromkeys(
            creator.full_name or creator.native_name
            for creator in creators
            if creator.full_name or creator.native_name
        )
    )
    aliases = [
        {"role": creator.role, "names": list(creator.names)}
        for creator in creators
        if creator.names
    ]
    return primary, aliases


def canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"sortOrder"}
    )
    hostname = (parsed.hostname or "").casefold()
    netloc = hostname + (f":{parsed.port}" if parsed.port is not None else "")
    return urlunsplit(
        (parsed.scheme.casefold() or "https", netloc, parsed.path.rstrip("/"), urlencode(query), "")
    )


def provider_identifier(provider: str, result: SourceSearchResult | str) -> str | None:
    if isinstance(result, SourceSearchResult):
        value = result.identifiers.get(PROVIDER_ID_KEYS.get(provider, ""))
        if value is not None:
            return str(value)
        url = result.url
    else:
        url = result
    parser = {
        "mangadex": parse_mangadex_id,
        "naver": parse_naver_id,
        "webtoons": parse_webtoons_id,
    }.get(provider)
    if parser is None:
        return None
    value = parser(url)
    return str(value) if value is not None else None


def dedupe_key(provider: str, result: SourceSearchResult) -> str:
    identifier = provider_identifier(provider, result)
    return f"id:{identifier}" if identifier else f"url:{canonical_url(result.url)}"


def _name_variants(value: str) -> set[str]:
    variants = {normalize_text(value)}
    variants.update(normalize_text(part) for part in re.split(r"[()/,]", value))
    return {item for item in variants if item}


def local_candidate_score(
    aliases: list[str],
    creator_aliases: list[str],
    start_year: int | None,
    candidate_titles: list[str],
    candidate_authors: list[str],
    candidate_year: int | None,
) -> dict[str, float]:
    normalized_aliases = [normalize_text(alias) for alias in aliases if normalize_text(alias)]
    normalized_titles = [
        normalize_text(title) for title in candidate_titles if normalize_text(title)
    ]
    exact = any(title == alias for title in normalized_titles for alias in normalized_aliases)
    similarity = max(
        (
            SequenceMatcher(None, title, alias).ratio()
            for title in normalized_titles
            for alias in normalized_aliases
        ),
        default=0.0,
    )
    token_overlap = max(
        (
            len(set(title.split()) & set(alias.split()))
            / len(set(title.split()) | set(alias.split()))
            for title in normalized_titles
            for alias in normalized_aliases
            if set(title.split()) | set(alias.split())
        ),
        default=0.0,
    )
    title_score = 100.0 if exact else similarity * 70 + token_overlap * 20

    anchor_names = (
        set().union(*(_name_variants(name) for name in creator_aliases))
        if creator_aliases
        else set()
    )
    candidate_names = (
        set().union(*(_name_variants(name) for name in candidate_authors))
        if candidate_authors
        else set()
    )
    overlaps = {
        (left, right)
        for left in anchor_names
        for right in candidate_names
        if left == right or (min(len(left), len(right)) >= 4 and (left in right or right in left))
    }
    creator_score = 20.0 if overlaps else 0.0
    if len(overlaps) >= 2:
        creator_score += 5.0

    year_score = 0.0
    if start_year is not None and candidate_year is not None:
        difference = abs(start_year - candidate_year)
        year_score = 10.0 if difference == 0 else (5.0 if difference == 1 else -10.0)

    anchor_markers = {
        marker
        for marker in RELATION_MARKERS
        if any(marker in alias for alias in normalized_aliases)
    }
    candidate_markers = {
        marker for marker in RELATION_MARKERS if any(marker in title for title in normalized_titles)
    }
    relation_penalty = -25.0 if candidate_markers - anchor_markers else 0.0
    return {
        "total": round(title_score + creator_score + year_score + relation_penalty, 6),
        "title": round(title_score, 6),
        "creator": creator_score,
        "year": year_score,
        "relation": relation_penalty,
    }


def deterministic_identifier_decision(
    anchor_identifiers: dict[str, int | str | None],
    candidate_identifiers: dict[str, int | str | None],
) -> tuple[str | None, list[str]]:
    evidence: list[str] = []
    same = False
    different = False
    for key in ("anilist_id", "mal_id"):
        anchor_value = anchor_identifiers.get(key)
        candidate_value = candidate_identifiers.get(key)
        if anchor_value is None or candidate_value is None:
            continue
        if str(anchor_value) == str(candidate_value):
            same = True
            evidence.append(f"same {key}={anchor_value}")
        else:
            different = True
            evidence.append(f"different {key}: {anchor_value} != {candidate_value}")
    if same and different:
        return "IDENTIFIER_CONFLICT", evidence
    if different:
        return "DIFFERENT_IDENTIFIER", evidence
    if same:
        return "SAME_IDENTIFIER", evidence
    return None, evidence


def deterministic_platform_decision(
    provider: str, result: SourceSearchResult
) -> tuple[str | None, list[str]]:
    if provider == "webtoons" and "/canvas/" in urlsplit(result.url).path.casefold():
        return "DIFFERENT_COMMUNITY_WORK", ["WEBTOON Canvas/community search result"]
    return None, []


def search_one(
    provider: str, alias: str, *, searcher: Callable[[str], list[SourceSearchResult]] | None = None
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        matches = (searcher or PROVIDERS[provider])(alias)
        return {
            "provider": provider,
            "alias": alias,
            "status": "ok",
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "result_count": len(matches),
            "error": None,
            "matches": matches,
        }
    except Exception as error:
        return {
            "provider": provider,
            "alias": alias,
            "status": "error",
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "result_count": 0,
            "error": f"{type(error).__name__}: {error}",
            "matches": [],
        }


def search_aliases(
    provider: str, aliases: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(aliases))) as executor:
        futures = [executor.submit(search_one, provider, alias) for alias in aliases]
        for future in as_completed(futures):
            events.append(future.result())
    return merge_alias_results(events, aliases, provider)


def merge_alias_results(
    events: list[dict[str, Any]], aliases: list[str], provider: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events.sort(key=lambda item: aliases.index(item["alias"]))

    merged: dict[str, dict[str, Any]] = {}
    for alias_index, event in enumerate(events):
        for rank, result in enumerate(event["matches"], 1):
            key = dedupe_key(provider, result)
            item = merged.setdefault(
                key,
                {
                    "provider": provider,
                    "result": result,
                    "titles": [],
                    "authors": [],
                    "matched_aliases": [],
                    "best_search_rank": rank,
                    "first_alias_index": alias_index,
                },
            )
            item["titles"] = list(dict.fromkeys([*item["titles"], result.title]))
            item["authors"] = list(dict.fromkeys([*item["authors"], *result.authors]))
            item["matched_aliases"] = list(
                dict.fromkeys([*item["matched_aliases"], event["alias"]])
            )
            item["best_search_rank"] = min(item["best_search_rank"], rank)
    for event in events:
        event.pop("matches", None)
    return events, list(merged.values())


def direct_sources(manga: AniListManga, client: AniListClient) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for source in client.extract_supported_sources(manga.external_links):
        provider = "naver" if source.provider == "naver_series" else source.provider
        grouped.setdefault(provider, []).append({**asdict(source), "provider_group": provider})
    return grouped


def anchor_candidate(manga: AniListManga, url: str) -> dict[str, Any]:
    aliases = title_aliases(manga)
    creators, creator_details = creator_names(manga)
    description = BeautifulSoup(manga.description or "", "lxml").get_text(" ", strip=True)
    return {
        "source": "anilist",
        "search_title": aliases[0] if aliases else f"AniList #{manga.id}",
        "url": url,
        "identifiers": {"anilist_id": manga.id, "mal_id": manga.id_mal},
        "extra": {},
        "status": "ok",
        "error": None,
        "metadata": {
            "title": aliases[0] if aliases else f"AniList #{manga.id}",
            "alternative_titles": aliases[1:],
            "authors": creators,
            "creator_aliases": creator_details,
            "genres": list(manga.genres),
            "description": description,
            "url": url,
            "start_year": manga.start_year,
            "format": manga.format,
        },
    }

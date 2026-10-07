"""Resolve source links for one explicitly selected AniList manga."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from . import catalog_matching as catalogs
from . import jev
from .anilist import AniListClient, AniListFieldSet, AniListManga
from .search import _to_search_item
from .search_common import SearchItem
from .sources.base_source import SourceSearchResult

logger = logging.getLogger(__name__)
MAX_REQUEST_WORKERS = 6
MAX_COMPARISON_WORKERS = 3
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    mode: Literal["external_only", "search_missing"]
    search: Callable[[str], list[SourceSearchResult]] | None = None


SOURCE_POLICIES: dict[str, ProviderPolicy] = {
    "mangadex": ProviderPolicy(
        "search_missing", lambda query: catalogs.PROVIDERS["mangadex"](query)
    ),
    "naver": ProviderPolicy("external_only"),
    "webtoons": ProviderPolicy("external_only"),
}


@dataclass(slots=True)
class SourceLink:
    provider: str
    url: str
    language: str | None
    method: str
    metadata_status: str
    metadata_error: str | None = None


@dataclass(slots=True)
class ProviderResolution:
    provider: str
    resolution: str = "NOT_RESOLVED"
    links: list[SourceLink] = field(default_factory=list)
    searches: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SourceSearchResponse:
    item: SearchItem
    threshold: float
    providers: list[ProviderResolution]
    events: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    timings: dict[str, Any] = field(default_factory=dict)

    @property
    def links(self) -> list[SourceLink]:
        return [link for provider in self.providers for link in provider.links]

    def asdict(self) -> dict[str, Any]:
        return {
            "item": self.item.asdict(),
            "threshold": self.threshold,
            "links": [asdict(link) for link in self.links],
            "providers": [asdict(provider) for provider in self.providers],
            "events": self.events,
            "errors": self.errors,
            "timings": self.timings,
        }


def validate_options(threshold: float, candidate_limit: int) -> None:
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 100
    ):
        raise ValueError("threshold must be a finite percentage between 0 and 100.")
    if (
        isinstance(candidate_limit, bool)
        or not isinstance(candidate_limit, int)
        or candidate_limit < 1
    ):
        raise ValueError("candidate_limit must be a positive integer.")


async def search_sources(
    anilist_id: int,
    *,
    threshold: float = jev.DEFAULT_THRESHOLD,
    candidate_limit: int = 3,
) -> SourceSearchResponse:
    """Resolve missing catalogs after selection; threshold uses percentages (0..100).

    Credentials are read from JEVMODEL_API_KEY and its numbered fallback variables.
    AniList lookup errors raise; individual source/JEV errors are returned in diagnostics.
    candidate_limit applies separately to primary and fallback phases.
    No chapter lists or chapter images are fetched; trusted links need no metadata request.
    """
    started = time.perf_counter()
    validate_options(threshold, candidate_limit)
    detail_started = time.perf_counter()
    try:
        async with AniListClient() as client:
            manga = await client.get_manga(anilist_id, fields=AniListFieldSet.MATCHING)
            direct = catalogs.direct_sources(manga, client)
            item = _to_search_item(client, manga)
    except Exception as error:
        write_search_log(
            {
                "event_type": "source_resolution",
                "created_at": jev.utc_now(),
                "anilist_id": anilist_id,
                "status": "error",
                "error": str(error),
                "timings": {"total_seconds": time.perf_counter() - started},
            }
        )
        raise
    detail_seconds = time.perf_counter() - detail_started
    # Only active, resolver-supported links belong in the merged item.
    item.urls = dict.fromkeys(item.urls)
    item.urls["anilist"] = manga.site_url or f"https://anilist.co/manga/{manga.id}"
    item.url = item.urls["anilist"]
    item.extra["externalLinks"] = [
        {"url": link["url"], "site": link["site"], "language": link["language"]}
        for links in direct.values()
        for link in links
    ]
    resolution_started = time.perf_counter()
    response = await asyncio.to_thread(
        _resolve, manga, direct, item, float(threshold), candidate_limit
    )
    response.timings.update(
        {
            "anilist_detail_seconds": detail_seconds,
            "resolution_seconds": time.perf_counter() - resolution_started,
            "total_seconds": time.perf_counter() - started,
        }
    )
    _save_runtime_log(response)
    return response


def _save_runtime_log(response: SourceSearchResponse) -> None:
    """Append redacted durations and decisions, never request headers or credentials."""
    row = {
        "event_type": "source_resolution",
        "created_at": jev.utc_now(),
        "anilist_id": response.item.identifiers["anilist_id"],
        "threshold": response.threshold,
        "timings": response.timings,
        "events": response.events,
        "errors": response.errors,
        "links": [asdict(link) for link in response.links],
    }
    write_search_log(row)


def write_search_log(row: dict[str, Any]) -> None:
    path = Path(os.environ.get("MANDOWN_SEARCH_LOG", "search_logs/search.jsonl"))

    def redact(value):
        if isinstance(value, str):
            for key in jev.jev_api_keys():
                value = value.replace(key, "[REDACTED]")
        elif isinstance(value, dict):
            return {key: redact(val) for key, val in value.items()}
        elif isinstance(value, (list, tuple)):
            return [redact(val) for val in value]
        return value

    row = redact(row)
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    except OSError as error:
        logger.warning("Search timing log write failed: %s", type(error).__name__)


class _KeyState:
    """Short critical sections; independent network calls never hold this lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.index = 0

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {"index": self.index}

    def advance(self, state: dict[str, int]) -> None:
        with self.lock:
            self.index = max(self.index, state["index"])


@dataclass(slots=True)
class _ProviderWork:
    resolution: ProviderResolution
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted: list[SourceSearchResult] = field(default_factory=list)
    timings: dict[str, Any] = field(default_factory=dict)


class _ProviderResolver:
    def __init__(
        self,
        manga: AniListManga,
        item: SearchItem,
        provider: str,
        policy: ProviderPolicy,
        threshold: float,
        candidate_limit: int,
        executor: ThreadPoolExecutor,
        keys: list[str],
        key_state: _KeyState,
    ):
        self.manga = manga
        self.title = item.title
        self.provider = provider
        self.policy = policy
        self.threshold = threshold
        self.candidate_limit = candidate_limit
        self.executor = executor
        self.keys = keys
        self.key_state = key_state
        self.work = _ProviderWork(ProviderResolution(provider))
        self.aliases = catalogs.title_aliases(manga)
        self.anchor = catalogs.anchor_candidate(manga, item.urls["anilist"] or item.url)
        self.creator_aliases = [name for creator in manga.creators for name in creator.names]
        self.started = time.perf_counter()
        self.attempted: set[str] = set()
        self.known_rejected: set[str] = set()
        self.comparison_slots = threading.Semaphore(MAX_COMPARISON_WORKERS)

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for key in self.keys:
                value = value.replace(key, "[REDACTED]")
        elif isinstance(value, dict):
            return {key: self.redact(val) for key, val in value.items()}
        elif isinstance(value, (list, tuple)):
            return [self.redact(val) for val in value]
        return value

    def event(self, decision: str, message: str, **details: Any) -> None:
        row = self.redact(
            {
                "provider": self.provider,
                "decision": decision,
                "message": message,
                "elapsed_seconds": time.perf_counter() - self.started,
                **details,
            }
        )
        self.work.events.append(row)
        logger.info("%s", row)

    def accept(
        self,
        result: SourceSearchResult,
        method: str,
        language: str | None = None,
        hydrated: dict[str, Any] | None = None,
    ) -> None:
        status = hydrated["status"] if hydrated is not None else "not_fetched"
        error = self.redact(hydrated.get("error")) if hydrated is not None else None
        self.work.resolution.links.append(
            SourceLink(self.provider, result.url, language, method, status, error)
        )
        self.work.accepted.append(result)
        self.work.resolution.resolution = method
        if hydrated is None:
            self.event(
                "METADATA_SKIPPED",
                "Güvenilir kimlik: metadata isteği yapılmadı.",
                url=result.url,
                metadata_status="not_fetched",
            )

    def primary_aliases(self) -> list[str]:
        # Select by normalized value: title_aliases has already deduplicated/capped the list.
        keys = {
            catalogs.normalize_text(name)
            for name in (self.manga.title.english, self.manga.title.romaji)
            if name
        }
        return [name for name in self.aliases if catalogs.normalize_text(name) in keys]

    def candidates(self, aliases: list[str], phase: str) -> list[dict[str, Any]]:
        futures = [
            self.executor.submit(
                catalogs.search_one, self.provider, alias, searcher=self.policy.search
            )
            for alias in aliases
        ]
        raw_events = [future.result() for future in futures]
        searches, candidates = catalogs.merge_alias_results(raw_events, aliases, self.provider)
        for search in searches:
            search["phase"] = phase
            self.work.resolution.searches.append(self.redact(search))
            details = {key: value for key, value in search.items() if key != "provider"}
            self.event(
                "SEARCH_ERROR" if search["status"] == "error" else "SEARCH_OK",
                "Kaynakta alias araması tamamlandı.",
                **details,
            )
        for candidate in candidates:
            result = candidate["result"]
            decision, evidence = catalogs.deterministic_platform_decision(self.provider, result)
            if decision is None:
                decision, evidence = catalogs.deterministic_identifier_decision(
                    self.anchor["identifiers"], result.identifiers
                )
            candidate["decision"] = decision
            candidate["evidence"] = evidence
            year = result.extra.get("year")
            candidate["local_score"] = catalogs.local_candidate_score(
                self.aliases,
                self.creator_aliases,
                self.manga.start_year,
                candidate["titles"],
                candidate["authors"],
                year if isinstance(year, int) else None,
            )
            candidate["phase"] = phase
        candidates.sort(
            key=lambda c: (
                c["decision"] == "SAME_IDENTIFIER",
                c["local_score"]["total"],
                -c["best_search_rank"],
                -c["first_alias_index"],
            ),
            reverse=True,
        )
        self.work.resolution.candidates.extend(
            self.redact(
                [
                    {
                        **{key: value for key, value in candidate.items() if key != "result"},
                        "title": candidate["result"].title,
                        "url": candidate["result"].url,
                        "identifiers": dict(candidate["result"].identifiers),
                    }
                    for candidate in candidates
                ]
            )
        )
        return candidates

    def compare_one(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Worker returns diagnostics; only the provider coordinator mutates results."""
        with self.comparison_slots:
            result = candidate["result"]
            started = time.perf_counter()
            hydrated = jev.hydrate_candidate(self.provider, result)
            metadata_seconds = time.perf_counter() - started
            if hydrated["status"] != "ok":
                return {
                    "hydrated": hydrated,
                    "metadata_seconds": metadata_seconds,
                    "comparison": None,
                    "jev_seconds": 0.0,
                }
            state = self.key_state.snapshot()
            started = time.perf_counter()
            try:
                comparison = jev.call_jev(
                    self.anchor, hydrated, self.keys, self.threshold, key_state=state
                )
            except Exception as error:
                comparison = {"status": "error", "error": str(error), "percentage": None}
            finally:
                self.key_state.advance(state)
            return {
                "hydrated": hydrated,
                "metadata_seconds": metadata_seconds,
                "comparison": comparison,
                "jev_seconds": time.perf_counter() - started,
            }

    def compare(self, candidates: list[dict[str, Any]], phase: str) -> bool:
        selected = []
        for candidate in candidates:
            key = catalogs.dedupe_key(self.provider, candidate["result"])
            if (
                candidate["decision"] is None
                and key not in self.attempted
                and key not in self.known_rejected
            ):
                selected.append(candidate)
                if len(selected) == self.candidate_limit:
                    break
        for candidate in selected:
            self.attempted.add(catalogs.dedupe_key(self.provider, candidate["result"]))
        jobs = [self.executor.submit(self.compare_one, candidate) for candidate in selected]
        accepted = []
        for candidate, job in zip(selected, jobs):
            result = candidate["result"]
            outcome = job.result()
            hydrated = outcome["hydrated"]
            if hydrated["status"] != "ok":
                self.event(
                    "METADATA_ERROR",
                    "Aday metaverisi alınamadı; aday kabul edilmedi.",
                    url=result.url,
                    phase=phase,
                    elapsed_operation_seconds=outcome["metadata_seconds"],
                    error=hydrated["error"],
                )
                continue
            self.event(
                "METADATA_OK",
                "Aday metaverisi alındı.",
                url=result.url,
                phase=phase,
                elapsed_operation_seconds=outcome["metadata_seconds"],
            )
            comparison = self.redact(
                {
                    **outcome["comparison"],
                    "candidate_url": result.url,
                    "candidate_title": result.title,
                    "threshold": self.threshold,
                    "phase": phase,
                    "jev_seconds": outcome["jev_seconds"],
                }
            )
            self.work.resolution.comparisons.append(comparison)
            if comparison["status"] != "ok":
                self.event(
                    "JEV_ERROR",
                    "JEV karşılaştırması başarısız; aday kabul edilmedi.",
                    url=result.url,
                    phase=phase,
                    error=comparison["error"],
                    elapsed_operation_seconds=outcome["jev_seconds"],
                )
                continue
            score = comparison["percentage"]
            self.event(
                "SAME_JEV" if score >= self.threshold else "DIFFERENT_JEV",
                "JEV puanı eşikle karşılaştırıldı.",
                url=result.url,
                phase=phase,
                percentage=score,
                threshold=self.threshold,
                elapsed_operation_seconds=outcome["jev_seconds"],
            )
            if score >= self.threshold:
                accepted.append((score, result, hydrated))
        if not accepted:
            return False
        _, best, hydrated = max(accepted, key=lambda pair: pair[0])
        self.accept(best, "SAME_JEV", hydrated=hydrated)
        return True

    def phase(self, aliases: list[str], name: str) -> bool:
        if not aliases:
            return False
        started = time.perf_counter()
        candidates = self.candidates(aliases, name)
        exact = None
        for candidate in candidates:
            decision = candidate["decision"]
            if decision:
                self.event(
                    decision,
                    "JEV çağrısı atlandı: kesin kimlik eşleşmesi"
                    if decision == "SAME_IDENTIFIER"
                    else "Aday reddedildi; JEV çağrısı atlandı.",
                    url=candidate["result"].url,
                    evidence=candidate["evidence"],
                    phase=name,
                )
                if decision == "SAME_IDENTIFIER" and exact is None:
                    exact = candidate
                elif decision != "SAME_IDENTIFIER":
                    self.known_rejected.add(catalogs.dedupe_key(self.provider, candidate["result"]))
        if exact is not None:
            self.accept(exact["result"], "SAME_IDENTIFIER")
            accepted = True
        else:
            accepted = self.compare(candidates, name)
        self.work.timings[name + "_seconds"] = time.perf_counter() - started
        return accepted

    def run(self, direct: list[dict[str, Any]]) -> _ProviderWork:
        try:
            if direct:
                for link in direct:
                    self.event(
                        "SAME_DIRECT_LINK",
                        "JEV ve kaynak araması atlandı: aktif AniList bağlantısı.",
                        url=link["url"],
                        trusted=True,
                    )
                    self.accept(
                        SourceSearchResult(self.title, link["url"]),
                        "SAME_DIRECT_LINK",
                        link["language"],
                    )
            elif self.policy.mode == "external_only":
                self.work.resolution.resolution = "SKIPPED_EXTERNAL_ONLY"
                self.event(
                    "SKIPPED_EXTERNAL_ONLY", "External link yok; kaynak araması ve JEV yapılmadı."
                )
            else:
                primary = self.primary_aliases()
                first_succeeded = self.phase(primary, "primary")
                primary_keys = {catalogs.normalize_text(name) for name in primary}
                fallback = [
                    name
                    for name in self.aliases
                    if catalogs.normalize_text(name) not in primary_keys
                ]
                if not first_succeeded and not self.phase(fallback, "fallback"):
                    self.event("NOT_RESOLVED", "Kabul edilen kaynak adayı bulunamadı.")
        except Exception as error:
            self.event("PROVIDER_ERROR", "Kaynak çözümleme başarısız.", error=str(error))
        self.work.timings["total_seconds"] = time.perf_counter() - self.started
        return self.work


def _resolve(manga, direct, item, threshold, candidate_limit) -> SourceSearchResponse:
    response = SourceSearchResponse(item, threshold, [], [], [])
    keys = jev.jev_api_keys()
    state = _KeyState()
    # Coordinators use a separate pool: none can block an I/O worker waiting for nested tasks.
    with ThreadPoolExecutor(max_workers=MAX_REQUEST_WORKERS) as requests_pool:
        with ThreadPoolExecutor(max_workers=max(1, min(6, len(SOURCE_POLICIES)))) as coordinators:
            jobs = [
                coordinators.submit(
                    _ProviderResolver(
                        manga,
                        item,
                        provider,
                        policy,
                        threshold,
                        candidate_limit,
                        requests_pool,
                        keys,
                        state,
                    ).run,
                    direct.get(provider, []),
                )
                for provider, policy in SOURCE_POLICIES.items()
            ]
            for job in jobs:
                work = job.result()
                resolution = work.resolution
                response.providers.append(resolution)
                response.events.extend(work.events)
                response.errors.extend(
                    event for event in work.events if event["decision"].endswith("ERROR")
                )
                response.timings[resolution.provider] = work.timings
                for result in work.accepted:
                    item.merge(SearchItem(resolution.provider, result))
                    if item.url == item.urls["anilist"]:
                        item.url = result.url
                if resolution.links:
                    item.urls[resolution.provider] = resolution.links[0].url
    return response

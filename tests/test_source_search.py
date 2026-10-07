"""Offline checks for progressive, external-only, parallel source discovery."""

import asyncio
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import pytest

import mandown
from mandown import catalog_matching as catalogs
from mandown import jev
from mandown import source_search as resolver
from mandown.anilist import (
    AniListClient,
    AniListExternalLink,
    AniListFieldSet,
    AniListManga,
    AniListTitle,
)
from mandown.sources.base_source import SourceSearchResult


@pytest.fixture
def setup(monkeypatch, tmp_path):
    calls = {"details": [], "search": [], "jev": [], "hydrate": []}
    manga = AniListManga(
        id=42,
        id_mal=72,
        title=AniListTitle(english="Example", romaji="Romaji", native="예"),
        synonyms=("EXAMPLE", "Alternative"),
        description="<p>Distinctive story</p>",
    )

    async def get_manga(self, ident, **kwargs):
        calls["details"].append((ident, kwargs))
        return manga

    monkeypatch.setattr(AniListClient, "get_manga", get_manga)
    for provider in catalogs.PROVIDERS:

        def search(alias, provider=provider):
            calls["search"].append((provider, alias))
            if provider in ("naver", "webtoons"):
                pytest.fail("external-only platform was searched")
            return []

        monkeypatch.setitem(catalogs.PROVIDERS, provider, search)

    def hydrate(provider, result):
        calls["hydrate"].append(result.url)
        return {
            "status": "ok",
            "error": None,
            "metadata": {"title": result.title},
            "source": provider,
            "search_title": result.title,
            "url": result.url,
            "identifiers": result.identifiers,
        }

    monkeypatch.setattr(jev, "hydrate_candidate", hydrate)
    monkeypatch.setattr(jev, "jev_api_keys", lambda: ["test-secret"])

    def compare(a, b, keys, threshold, **kwargs):
        calls["jev"].append((b["url"], threshold))
        return {"status": "ok", "percentage": 60.0, "error": None, "decision": "SAME"}

    monkeypatch.setattr(jev, "call_jev", compare)
    monkeypatch.setenv("MANDOWN_SEARCH_LOG", str(tmp_path / "search.jsonl"))
    return calls, manga


def candidate(index=1, identifiers=None):
    return SourceSearchResult(
        "Example",
        f"https://mangadex.org/title/00000000-0000-0000-0000-{index:012d}",
        identifiers=identifiers or {},
    )


def direct_naver():
    return AniListExternalLink("Naver", "https://comic.naver.com/webtoon/list?titleId=1")


def run(**kwargs):
    return asyncio.run(mandown.search_sources(42, **kwargs))


def test_detail_fields_policies_and_no_production_experiment_imports(setup):
    calls, _ = setup
    result = run()
    assert calls["details"] == [(42, {"fields": AniListFieldSet.MATCHING})]
    assert result.threshold == 54.0
    assert result.item.identifiers["anilist_id"] == 42
    assert not calls["jev"]
    assert [p.resolution for p in result.providers] == [
        "NOT_RESOLVED",
        "SKIPPED_EXTERNAL_ONLY",
        "SKIPPED_EXTERNAL_ONLY",
    ]
    assert {provider for provider, _ in calls["search"]} == {"mangadex"}
    for name in ("jev.py", "catalog_matching.py", "source_search.py"):
        assert "import test_jev" not in (Path(mandown.__file__).parent / name).read_text()


def test_trusted_direct_links_require_no_io_and_retain_languages(setup):
    calls, manga = setup
    links = (
        AniListExternalLink(
            "WEBTOON",
            "https://www.webtoons.com/en/action/example/list?title_no=1",
            language="English",
        ),
        AniListExternalLink(
            "WEBTOON",
            "https://www.webtoons.com/tr/action/example/list?title_no=2",
            language="Turkish",
        ),
        direct_naver(),
        AniListExternalLink("MangaDex", candidate().url),
    )
    object.__setattr__(manga, "external_links", links)
    result = run()
    assert calls["search"] == calls["jev"] == calls["hydrate"] == []
    assert len(result.links) == 4
    assert {link.language for link in result.links if link.provider == "webtoons"} == {
        "English",
        "Turkish",
    }
    assert all(link.metadata_status == "not_fetched" for link in result.links)
    assert all(p.resolution == "SAME_DIRECT_LINK" for p in result.providers)
    assert result.item.urls["webtoons"] == links[0].url
    assert result.item.sources == ["anilist", "mangadex", "naver", "webtoons"]


def test_disabled_links_do_not_search_external_only_platform(setup):
    calls, manga = setup
    object.__setattr__(
        manga,
        "external_links",
        (
            AniListExternalLink(
                "WEBTOON", "https://www.webtoons.com/en/a/b/list?title_no=1", is_disabled=True
            ),
        ),
    )
    result = run()
    assert result.item.urls["webtoons"] is None
    assert result.item.extra["externalLinks"] == []
    assert not any(provider == "webtoons" for provider, _ in calls["search"])
    assert result.providers[2].resolution == "SKIPPED_EXTERNAL_ONLY"


def test_identifier_bypass_is_explicit_and_has_no_metadata(setup, monkeypatch, caplog):
    calls, _ = setup
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(identifiers={"anilist_id": 42})]
    )
    with caplog.at_level(logging.INFO, logger="mandown.source_search"):
        result = run()
    assert calls["jev"] == calls["hydrate"] == []
    row = next(event for event in result.events if event["decision"] == "SAME_IDENTIFIER")
    assert row["evidence"] == ["same anilist_id=42"]
    assert row["message"] == "JEV çağrısı atlandı: kesin kimlik eşleşmesi"
    assert row["message"] in caplog.text and "anilist_id=42" in caplog.text
    assert result.item.urls["mangadex"] == candidate().url
    assert result.links[0].metadata_status == "not_fetched"


@pytest.mark.parametrize(
    "ids,decision",
    [
        ({"anilist_id": 99}, "DIFFERENT_IDENTIFIER"),
        ({"anilist_id": 42, "mal_id": 99}, "IDENTIFIER_CONFLICT"),
    ],
)
def test_identifier_conflicts_are_rejected(setup, monkeypatch, ids, decision):
    calls, _ = setup
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(identifiers=ids)])
    result = run()
    assert calls["jev"] == calls["hydrate"] == []
    assert not result.links
    assert any(event["decision"] == decision for event in result.events)


@pytest.mark.parametrize("score,accepted", [(53.99, False), (54.0, True), (54.01, True)])
def test_threshold_boundaries_and_no_repeated_candidates(setup, monkeypatch, score, accepted):
    calls, _ = setup
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: [candidate()])
    comparisons = []

    def compare(*args, **kwargs):
        comparisons.append(args)
        return {"status": "ok", "percentage": score, "error": None}

    monkeypatch.setattr(jev, "call_jev", compare)
    result = run()
    assert bool(result.item.urls["mangadex"]) == accepted
    assert len(comparisons) == 1
    assert calls["hydrate"] == [candidate().url]
    assert result.providers[0].comparisons[0]["threshold"] == 54


def test_highest_score_and_stable_ties(setup, monkeypatch):
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(i) for i in range(1, 6)]
    )
    result = run(threshold=60, candidate_limit=2)
    assert result.item.urls["mangadex"] == candidate(1).url
    assert len(result.providers[0].comparisons) == 2
    scores = {candidate(1).url: 60, candidate(2).url: 95, candidate(3).url: 80}
    monkeypatch.setattr(
        jev,
        "call_jev",
        lambda a, b, *args, **kwargs: {
            "status": "ok",
            "percentage": scores[b["url"]],
            "error": None,
        },
    )
    assert run().item.urls["mangadex"] == candidate(2).url


@pytest.mark.parametrize("threshold", [-1, 101, float("nan"), float("inf"), "54", None, True])
def test_invalid_threshold_precedes_network(setup, threshold):
    calls, _ = setup
    with pytest.raises(ValueError, match="threshold"):
        run(threshold=threshold)
    assert not calls["details"]


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_invalid_candidate_limit_precedes_network(setup, limit):
    calls, _ = setup
    with pytest.raises(ValueError, match="candidate_limit"):
        run(candidate_limit=limit)
    assert not calls["details"]


def test_missing_keys_never_accept_title_but_do_accept_exact_identity(setup, monkeypatch):
    # Exercise the real key/cache gate rather than the unconditional comparison stub.
    from mandown.jev import _call_jev_uncached

    monkeypatch.setattr(jev, "call_jev", _call_jev_uncached)
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: [candidate()])
    monkeypatch.setattr(jev, "jev_api_keys", lambda: [])
    result = run()
    assert not result.links
    assert any(event["decision"] == "JEV_ERROR" for event in result.errors)
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(identifiers={"mal_id": 72})]
    )
    assert run().providers[0].resolution == "SAME_IDENTIFIER"


def test_source_and_jev_errors_preserve_trusted_links_and_redact(
    setup, monkeypatch, caplog, tmp_path
):
    _, manga = setup
    object.__setattr__(manga, "external_links", (direct_naver(),))

    def fail(alias):
        raise ValueError("provider test-secret error")

    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", fail)
    with caplog.at_level(logging.INFO, logger="mandown.source_search"):
        result = run()
    assert result.item.urls["naver"] == direct_naver().url
    assert (
        "test-secret"
        not in json.dumps(result.asdict()) + caplog.text + (tmp_path / "search.jsonl").read_text()
    )
    assert any(event["decision"] == "SEARCH_ERROR" for event in result.errors)
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: [candidate()])

    def bad_jev(*args, **kwargs):
        raise ValueError("JEV test-secret error")

    monkeypatch.setattr(jev, "call_jev", bad_jev)
    result = run()
    assert result.item.urls["naver"] == direct_naver().url
    assert any(event["decision"] == "JEV_ERROR" for event in result.errors)
    assert "test-secret" not in json.dumps(result.asdict())


def test_failed_metadata_does_not_call_jev(setup, monkeypatch):
    calls, _ = setup
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: [candidate()])
    monkeypatch.setattr(
        jev,
        "hydrate_candidate",
        lambda *args: {"status": "error", "error": "timeout", "metadata": None},
    )
    result = run()
    assert not result.links and not calls["jev"]
    assert any(event["decision"] == "METADATA_ERROR" for event in result.errors)


def test_empty_titles_skip_search(setup):
    calls, manga = setup
    object.__setattr__(manga, "title", AniListTitle())
    object.__setattr__(manga, "synonyms", ())
    run()
    assert not calls["search"]


def test_calibrated_threshold_and_prompt_are_stable():
    assert jev.DEFAULT_THRESHOLD == 54.0
    assert hashlib.sha256(jev.MATCH_INSTRUCTIONS.encode()).hexdigest() == (
        "a66c3474bc2e319537e2e2deef88dc93ce403be24ab915d4f05bc0312825404b"
    )


def test_primary_titles_are_parallel_and_stop_fallback_on_verified_match(setup, monkeypatch):
    calls, _ = setup
    barrier = threading.Barrier(2)
    seen = []

    def search(alias):
        seen.append(alias)
        barrier.wait(timeout=2)
        return [candidate(identifiers={"anilist_id": 42})]

    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", search)
    result = run()
    assert set(seen) == {"Example", "Romaji"}
    assert not calls["hydrate"] and not calls["jev"]
    assert result.providers[0].resolution == "SAME_IDENTIFIER"
    assert all(s["phase"] == "primary" for s in result.providers[0].searches)


def test_title_dedupe_and_fallback_cap(setup, monkeypatch):
    _, manga = setup
    object.__setattr__(
        manga, "title", AniListTitle(english="Example", romaji="EXAMPLE", native="예")
    )
    object.__setattr__(manga, "synonyms", (" Example ", *(f"alt{i}" for i in range(20))))
    seen = []
    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", lambda alias: seen.append(alias) or [])
    result = run()
    assert len(seen) == 12 and len({catalogs.normalize_text(name) for name in seen}) == 12
    assert seen[0] == "Example"
    assert sum(s["phase"] == "primary" for s in result.providers[0].searches) == 1


def test_alternatives_are_parallel_after_unverified_primary_results(setup, monkeypatch):
    calls, manga = setup
    object.__setattr__(
        manga, "title", AniListTitle(english="Example", romaji="Romaji", native=None)
    )
    object.__setattr__(manga, "synonyms", tuple(f"alt{i}" for i in range(6)))
    barrier = threading.Barrier(6)

    def search(alias):
        if alias in ("Example", "Romaji"):
            return [candidate(1)]
        barrier.wait(timeout=2)
        return [candidate(2, identifiers={"anilist_id": 42})]

    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", search)
    monkeypatch.setattr(
        jev, "call_jev", lambda *args, **kwargs: {"status": "ok", "percentage": 10, "error": None}
    )
    result = run()
    assert result.item.urls["mangadex"] == candidate(2).url
    assert len(result.providers[0].searches) == 8
    assert calls["hydrate"] == [candidate(1).url]


def test_three_comparisons_per_phase_with_no_candidate_repetition(setup, monkeypatch):
    calls, _ = setup

    def search(alias):
        if alias in ("Example", "Romaji"):
            return [candidate(i) for i in (1, 2, 3)]
        return [candidate(i) for i in (1, 2, 3, 4, 5, 6)]

    monkeypatch.setitem(catalogs.PROVIDERS, "mangadex", search)

    def compare(a, b, *args, **kwargs):
        calls["jev"].append(b["url"])
        return {"status": "ok", "percentage": 10, "error": None}

    monkeypatch.setattr(jev, "call_jev", compare)
    result = run()
    assert len(calls["jev"]) == len(set(calls["jev"])) == 6
    assert [c["phase"] for c in result.providers[0].comparisons] == ["primary"] * 3 + [
        "fallback"
    ] * 3


def test_candidate_pipeline_runs_parallel_without_refetching_winner(setup, monkeypatch):
    calls, _ = setup
    barrier = threading.Barrier(3)
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(i) for i in (1, 2, 3)]
    )
    original = jev.hydrate_candidate

    def hydrate(*args):
        barrier.wait(timeout=2)
        return original(*args)

    monkeypatch.setattr(jev, "hydrate_candidate", hydrate)
    result = run()
    assert result.item.urls["mangadex"] == candidate(1).url
    assert len(calls["hydrate"]) == len(calls["jev"]) == 3


def test_future_provider_registry_is_parallel_and_deterministic(setup, monkeypatch):
    barrier = threading.Barrier(2)

    def search(alias):
        if alias == "Example":
            barrier.wait(timeout=2)
        return [
            SourceSearchResult(
                "Example", "https://other.test/work/42", identifiers={"anilist_id": 42}
            )
        ]

    monkeypatch.setattr(
        resolver,
        "SOURCE_POLICIES",
        {
            "future_a": resolver.ProviderPolicy("search_missing", search),
            "future_b": resolver.ProviderPolicy("search_missing", search),
        },
    )
    result = run()
    assert [p.provider for p in result.providers] == ["future_a", "future_b"]
    assert len(result.links) == 2
    assert all(p.resolution == "SAME_IDENTIFIER" for p in result.providers)


def test_shared_request_cap_across_future_providers(setup, monkeypatch):
    active = peak = 0
    lock = threading.Lock()

    def search(alias):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.025)
        with lock:
            active -= 1
        return []

    monkeypatch.setattr(
        resolver,
        "SOURCE_POLICIES",
        {
            f"future_{index}": resolver.ProviderPolicy("search_missing", search)
            for index in range(8)
        },
    )
    run()
    assert 2 <= peak <= 6


def test_parallel_failover_snapshots_never_regress(setup, monkeypatch):
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(i) for i in (1, 2, 3)]
    )
    states = []
    barrier = threading.Barrier(3)

    def compare(a, b, keys, threshold, *, key_state):
        states.append(key_state)
        barrier.wait(timeout=2)
        key_state["index"] = int(b["url"][-1])
        return {"status": "ok", "percentage": 10, "error": None}

    monkeypatch.setattr(jev, "call_jev", compare)
    run()
    assert len({id(state) for state in states}) == 3
    state = resolver._KeyState()
    state.advance({"index": 3})
    state.advance({"index": 1})
    assert state.snapshot() == {"index": 3}


def test_timing_log_and_response_share_decisions_without_keys(setup, tmp_path):
    result = run()
    row = json.loads((tmp_path / "search.jsonl").read_text())
    assert row["anilist_id"] == 42
    assert row["timings"] == result.timings
    assert row["events"] == result.events
    assert result.timings["total_seconds"] >= result.timings["resolution_seconds"]
    assert result.timings["anilist_detail_seconds"] > 0
    assert "primary_seconds" in result.timings["mangadex"]
    assert "test-secret" not in json.dumps(row)


def test_controlled_delay_demonstrates_parallel_speedup(setup, monkeypatch):
    monkeypatch.setitem(
        catalogs.PROVIDERS, "mangadex", lambda alias: [candidate(i) for i in (1, 2, 3)]
    )
    hydrate_original = jev.hydrate_candidate

    def hydrate(*args):
        time.sleep(0.04)
        return hydrate_original(*args)

    compare_original = jev.call_jev

    def compare(*args, **kwargs):
        time.sleep(0.04)
        return compare_original(*args, **kwargs)

    monkeypatch.setattr(jev, "hydrate_candidate", hydrate)
    monkeypatch.setattr(jev, "call_jev", compare)
    start = time.perf_counter()
    parallel = run()
    parallel_elapsed = time.perf_counter() - start
    monkeypatch.setattr(resolver, "MAX_REQUEST_WORKERS", 1)
    monkeypatch.setattr(resolver, "MAX_COMPARISON_WORKERS", 1)
    start = time.perf_counter()
    serial = run()
    serial_elapsed = time.perf_counter() - start
    assert parallel_elapsed < serial_elapsed * 0.75
    assert parallel.item.urls == serial.item.urls


def test_unwritable_log_does_not_discard_result(setup, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MANDOWN_SEARCH_LOG", str(tmp_path))  # A directory, not a JSONL file.
    run()
    assert "Search timing log write failed: IsADirectoryError" in caplog.text

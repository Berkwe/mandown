"""Offline persistent-score, seed and resolver integration regression tests."""

import asyncio
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mandown import JevCache, jev


def record(title="Fictional Star", url="https://example.test/1"):
    return {
        "source": "test",
        "search_title": title,
        "url": url,
        "identifiers": {},
        "metadata": {"title": title},
    }


class Session:
    def __init__(self, probability=0.7, status=200):
        self.calls = 0
        self.probability = probability
        self.status = status

    def post(self, *args, **kwargs):
        self.calls += 1
        time.sleep(0.01)
        outer = self

        class Response:
            status_code = outer.status
            headers = {}
            text = "fixture failure"

            def json(self):
                return {"answers": {jev.QUESTION_NAME: {"noul": outer.probability}}}

        return Response()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDOWN_JEV_CACHE", str(tmp_path / "cache.sqlite3"))
    return JevCache(tmp_path / "cache.sqlite3")


def compare(session, cache=None, *, keys="fixture-key", threshold=54, **kwargs):
    return jev.call_jev(
        record(),
        record(url="https://example.test/2"),
        keys,
        threshold,
        session=session,
        cache=cache,
        **kwargs,
    )


def test_persistent_hit_without_keys_rechecks_threshold(cache):
    session = Session()
    first = compare(session)
    assert first["decision"] == "SAME" and not first["cache_hit"]
    second = compare(session, keys=[], threshold=80)
    assert second["decision"] == "DIFFERENT" and second["cache_hit"]
    assert second["attempts"] == second["input_tokens"] == 0
    assert session.calls == 1


def test_restart_and_seed_without_credentials(cache, tmp_path):
    session = Session()
    compare(session)
    code = (
        "from mandown import jev, JevCache; "
        f"a={record()!r}; b={record(url='https://example.test/2')!r}; "
        f"r=jev.call_jev(a,b,[],54,cache=JevCache({str(cache.path)!r})); "
        "assert r['cache_hit'] and r['probability']==0.7 and r['attempts']==0"
    )
    subprocess.run([__import__("sys").executable, "-c", code], check=True)
    seed = tmp_path / "seed.json"
    assert cache.export_seed(seed) == 1
    assert "fixture-key" not in seed.read_text()
    assert "record_a" not in seed.read_text()
    other = JevCache(tmp_path / "other.sqlite3")
    assert other.import_seed(seed) == 1
    assert compare(session, other, keys=[])["cache_hit"]
    assert session.calls == 1


@pytest.mark.parametrize("probability", [0.1, 0.9])
def test_positive_and_negative_scores_cached(cache, probability):
    session = Session(probability)
    compare(session, cache)
    assert compare(session, cache, keys=[])["cache_hit"]
    assert session.calls == 1


def test_expiry_and_errors_not_cached(cache):
    session = Session(status=500)
    assert compare(session, cache)["status"] == "error"
    assert compare(session, cache)["status"] == "error"
    assert session.calls == 2
    session.status = 200
    result = compare(session, cache)
    with cache._connect() as connection:
        connection.execute("UPDATE scores SET created=?", (time.time() - cache.ttl - 1,))
    assert cache.get(result["cache_key"]) is None
    assert not compare(session, cache)["cache_hit"]
    assert session.calls == 4


def test_fingerprint_invalidations(cache, monkeypatch):
    session = Session()
    compare(session, cache)
    assert not compare(session, cache, instructions="different prompt")["cache_hit"]
    monkeypatch.setenv("MANDOWN_JEV_CACHE_REVISION", "2")
    assert not compare(session, cache)["cache_hit"]
    result = jev.call_jev(
        record("Changed metadata"), record(), "key", 54, session=session, cache=cache
    )
    assert not result["cache_hit"]
    monkeypatch.setattr(jev, "JEV_MODEL", "next-model")
    assert not compare(session, cache)["cache_hit"]
    assert session.calls == 5


def test_same_pair_concurrent_requests_coalesced(cache):
    session = Session()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: compare(session, cache), range(8)))
    assert session.calls == 1
    assert sum(r["cache_hit"] for r in results) == 7


def test_distinct_pairs_run_concurrently(cache):
    barrier = threading.Barrier(2)

    class ParallelSession(Session):
        def post(self, *args, **kwargs):
            barrier.wait(timeout=3)
            return super().post(*args, **kwargs)

    def run_pair(index):
        return jev.call_jev(
            record(str(index)), record(), "key", 54, session=ParallelSession(), cache=cache
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert all(r["status"] == "ok" for r in executor.map(run_pair, [1, 2]))


def test_disabled_and_broken_cache_fall_back(cache, monkeypatch, tmp_path):
    session = Session()
    monkeypatch.setenv("MANDOWN_JEV_CACHE", "off")
    compare(session)
    compare(session)
    broken = tmp_path / "broken"
    broken.write_text("not an sqlite database")
    compare(session, JevCache(broken))
    assert session.calls == 3
    with pytest.raises(ValueError, match="API key"):
        compare(session, keys=[], cache=False)


def test_seed_validation_atomic_and_preserves_newer(cache, tmp_path):
    result = compare(Session(), cache)
    seed = tmp_path / "seed.json"
    cache.export_seed(seed)
    payload = json.loads(seed.read_text())
    payload["scores"].append({"key": "bad"})
    seed.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        cache.import_seed(seed)
    assert cache.get(result["cache_key"])["probability"] == 0.7
    payload["scores"].pop()
    payload["scores"][0]["created"] -= 10
    payload["scores"][0]["probability"] = 0.1
    seed.write_text(json.dumps(payload))
    cache.import_seed(seed)
    assert cache.get(result["cache_key"])["probability"] == 0.7


@pytest.mark.parametrize("threshold", [-1, 101, float("nan"), True])
def test_invalid_threshold(cache, threshold):
    with pytest.raises(ValueError):
        compare(Session(), cache, threshold=threshold)


def test_resolver_cache_hit_without_api_keys(cache, monkeypatch, tmp_path):
    from mandown import catalog_matching as catalogs
    from mandown import source_search
    from mandown.anilist import AniListClient, AniListManga, AniListTitle
    from mandown.sources.base_source import SourceSearchResult

    async def get_manga(self, ident, **kwargs):
        return AniListManga(id=42, title=AniListTitle(english="Fictional Star"))

    monkeypatch.setattr(AniListClient, "get_manga", get_manga)
    monkeypatch.setitem(
        catalogs.PROVIDERS,
        "mangadex",
        lambda _: [SourceSearchResult("Fictional Star", "https://mangadex.org/title/fixture")],
    )
    monkeypatch.setattr(
        jev,
        "hydrate_candidate",
        lambda provider, result: {
            **record(url=result.url),
            "status": "ok",
            "error": None,
        },
    )
    monkeypatch.setenv("MANDOWN_SEARCH_LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(jev, "jev_api_keys", lambda: ["fixture-key"])
    original = jev.call_jev
    session = Session()
    monkeypatch.setattr(jev, "call_jev", lambda *a, **kw: original(*a, **kw, session=session))
    first = asyncio.run(source_search.search_sources(42))
    assert first.links
    monkeypatch.setattr(jev, "jev_api_keys", lambda: [])
    second = asyncio.run(source_search.search_sources(42))
    assert second.links == first.links
    assert second.providers[0].comparisons[0]["cache_hit"]
    assert session.calls == 1


def test_four_sources_second_pass_has_zero_api_calls(cache):
    session = Session()
    for _ in range(2):
        for index in range(4):
            result = jev.call_jev(
                record(),
                record(url=f"https://example.test/source/{index}"),
                "key" if _ == 0 else [],
                54,
                session=session,
                cache=cache,
            )
            assert result["cache_hit"] is (_ == 1)
    assert session.calls == 4


@pytest.mark.parametrize("probability", [float("nan"), -0.1, 1.1])
def test_invalid_api_scores_not_cached(cache, probability):
    session = Session(probability)
    assert compare(session, cache)["status"] == "error"
    assert compare(session, cache)["status"] == "error"
    assert session.calls == 2

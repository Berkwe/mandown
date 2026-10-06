"""Shared JEV metadata matching, calibrated against the latest AniList run."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import sources
from .sources.base_source import SourceSearchResult

# AniList-anchored run 20260928_133031_887036, recommended.threshold (not runtime 45).
# Prompt SHA256: a66c3474bc2e319537e2e2deef88dc93ce403be24ab915d4f05bc0312825404b
DEFAULT_THRESHOLD = 54.0

JEV_URL = "https://jevmodel.org/v1/systemone"
JEV_MODEL = "jev-latest"
QUESTION_NAME = "same_original_work"
MAX_STATE_CHARS = 7_800
DESCRIPTION_LIMIT = 2_400
RETRYABLE_STATUSES = {429, 502}
MATCH_INSTRUCTIONS = """Estimate P(yes) that record_a and record_b are the same underlying
original webtoon, not merely related works.

- Normalize translated/localized/alternative titles and creator aliases, name order, spelling,
  pen names, and transliteration. Descriptions may be translated, shortened, or paraphrased.
- Missing fields and a subset of creator credits are neutral/compatible, not conflicts. A
  compatible title + creator identity + distinctive synopsis is strong evidence even without a
  shared ID. A shared canonical catalog ID or URL is decisive evidence.
- Catalog titles can contain typos. The same creator plus the same named protagonist, distinctive
  goal/events, and a compatible title is strong identity evidence despite a title typo.
- Translations, localized releases, and book/other editions are SAME if the underlying webtoon is
  unchanged.
- Pre-serialization/pilot/prototype vs final, remake/reboot, spin-off/side story, sequel/prequel,
  adaptation to another medium, fan work, parody, or doujinshi are DIFFERENT.
- Explicit relation markers such as pre-serialization, pilot, prototype, spin-off, side story,
  gaiden, `oejeon`/`외전`, sequel, or fancomic override recycled titles, creators, and synopses:
  those records are DIFFERENT from the main/final work.
- Shared title, genre, theme, characters, or premise alone is weak. Conflicting creators,
  identifiers, publication facts, or clearly different plots are strong negative evidence.

Estimate identity neutrally from all evidence; thresholding is handled separately. Return the
probability that the answer is yes."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without adding a runtime dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def jev_api_keys() -> list[str]:
    """Return configured Jev keys in failover order without exposing their values."""
    names = ["JEVMODEL_API_KEY", "JEVMODEL_API_KEY_FALLBACK"]
    names.extend(f"JEVMODEL_API_KEY_FALLBACK_{index}" for index in range(2, 10))
    return [value for name in names if (value := os.environ.get(name, "").strip())]


def hydrate_candidate(provider: str, result: SourceSearchResult) -> dict[str, Any]:
    started_at = utc_now()
    started = time.perf_counter()
    base = {
        "source": provider,
        "search_title": result.title,
        "url": result.url,
        "identifiers": dict(result.identifiers),
        "extra": dict(result.extra),
        "started_at": started_at,
    }
    try:
        adapter = sources.get_class_for(result.url)(result.url)
        metadata = adapter.metadata
        return {
            **base,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "status": "ok",
            "error": None,
            "metadata": {
                "title": metadata.title,
                "authors": list(metadata.authors),
                "genres": list(metadata.genres),
                "description": metadata.description,
                "url": metadata.url,
                "start_year": (
                    result.extra.get("year") if isinstance(result.extra.get("year"), int) else None
                ),
            },
        }
    except Exception as error:
        return {
            **base,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
            "metadata": None,
        }


def candidate_record(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate["metadata"] or {}
    record = {
        "source": candidate["source"],
        "title": metadata.get("title") or candidate["search_title"],
        "authors_or_artists": metadata.get("authors") or [],
        "genres": metadata.get("genres") or [],
        "description": str(metadata.get("description") or "")[:DESCRIPTION_LIMIT],
        "url": metadata.get("url") or candidate["url"],
        "identifiers": {
            key: value for key, value in candidate["identifiers"].items() if value is not None
        },
    }
    for key in ("alternative_titles", "creator_aliases", "start_year", "format"):
        value = metadata.get(key)
        if value not in (None, [], (), ""):
            record[key] = value
    return record


def build_state(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    state = {"record_a": candidate_record(left), "record_b": candidate_record(right)}
    while len(json.dumps(state, ensure_ascii=False)) > MAX_STATE_CHARS:
        descriptions = [state["record_a"]["description"], state["record_b"]["description"]]
        longest = 0 if len(descriptions[0]) >= len(descriptions[1]) else 1
        record_key = "record_a" if longest == 0 else "record_b"
        description = state[record_key]["description"]
        if len(description) <= 200:
            raise ValueError("Jev state exceeds the size limit even after description truncation")
        state[record_key]["description"] = description[: max(200, len(description) // 2)]
    return state


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            return str(payload["error"].get("message") or payload["error"])
    except (ValueError, requests.exceptions.JSONDecodeError):
        pass
    return response.text[:500] or f"HTTP {response.status_code}"


def call_jev(
    left: dict[str, Any],
    right: dict[str, Any],
    api_key: str | list[str],
    threshold: float,
    *,
    instructions: str = MATCH_INSTRUCTIONS,
    session: Any = requests,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = 3,
    key_state: dict[str, int] | None = None,
) -> dict[str, Any]:
    api_keys = [api_key] if isinstance(api_key, str) else [key for key in api_key if key]
    if not api_keys:
        raise ValueError("At least one Jev API key is required")
    state = build_state(left, right)
    payload = {
        "model": JEV_MODEL,
        "state": state,
        "questions": {
            QUESTION_NAME: {
                "type": "noul",
                "instructions": instructions,
                "criteria": {
                    "true": "The records represent the same original webtoon work.",
                    "false": "The records do not represent the same original webtoon work.",
                },
            }
        },
    }
    fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    idempotency_key = f"mandown-jev-{hashlib.sha256(fingerprint).hexdigest()[:32]}"
    started_at = utc_now()
    started = time.perf_counter()
    attempts = 0
    key_index = min((key_state or {}).get("index", 0), len(api_keys) - 1)
    response: requests.Response | None = None
    try:
        while True:
            attempts += 1
            response = session.post(
                JEV_URL,
                headers={
                    "Authorization": f"Bearer {api_keys[key_index]}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
                json=payload,
                timeout=60,
            )
            if response.status_code == 402 and key_index + 1 < len(api_keys):
                key_index += 1
                if key_state is not None:
                    key_state["index"] = key_index
                continue
            if response.status_code not in RETRYABLE_STATUSES or attempts > max_retries:
                break
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempts - 1)
            )
            sleep(delay)

        if response.status_code >= 400:
            raise RuntimeError(f"Jev HTTP {response.status_code}: {_error_message(response)}")
        body = response.json()
        answer = body["answers"][QUESTION_NAME]
        probability = float(answer["noul"])
        if not 0 <= probability <= 1:
            raise ValueError(f"Jev noul probability is outside 0..1: {probability}")
        percentage = probability * 100
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        if key_state is not None:
            key_state["index"] = key_index
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "status": "ok",
            "attempts": attempts,
            "error": None,
            "model": body.get("model", JEV_MODEL),
            "probability": probability,
            "percentage": round(percentage, 4),
            "decision": "SAME" if percentage >= threshold else "DIFFERENT",
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "tokens_remaining": response.headers.get("X-Tokens-Remaining"),
            "api_key_slot": key_index + 1,
            "record_a": state["record_a"],
            "record_b": state["record_b"],
        }
    except Exception as error:
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "status": "error",
            "attempts": attempts,
            "error": f"{type(error).__name__}: {error}",
            "model": JEV_MODEL,
            "probability": None,
            "percentage": None,
            "decision": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens_remaining": response.headers.get("X-Tokens-Remaining") if response else None,
            "api_key_slot": key_index + 1,
            "record_a": state["record_a"],
            "record_b": state["record_b"],
        }

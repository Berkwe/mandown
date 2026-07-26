import pytest
from common import is_source_working, skip_in_ci

from mandown.base import BaseChapter
from mandown.errors import SourceResponseError
from mandown.sources.source_mangadex import MangaDexSource

DESCRIPTION = """All’s fair when love is war!

Two geniuses. Two brains. Two hearts. One battle. Who will confess their love first…?!

Kaguya Shinomiya and Miyuki Shirogane are two geniuses who stand atop their prestigious academy’s student council, making them the elite among elite. But it’s lonely at the top and each has fallen for the other. There’s just one huge problem standing in the way of lovey-dovey bliss—they’re both too prideful to be the first to confess their romantic feelings and thus become the “loser” in the competition of love! And so begins their daily schemes to force the other to confess first!"""


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def source() -> MangaDexSource:
    return MangaDexSource(
        "https://mangadex.org/title/37f5cce0-8070-4ada-96e5-fa24b1bd4ff9"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"relationships": []}},
        {"data": {"relationships": {}}},
        {"data": {"relationships": [None, [], {"type": "manga", "id": 123}]}},
    ],
)
def test_chapter_url_reports_missing_manga_relationship(
    monkeypatch: pytest.MonkeyPatch,
    payload,
) -> None:
    monkeypatch.setattr(
        MangaDexSource,
        "_get",
        staticmethod(lambda url: FakeResponse(payload)),
    )

    with pytest.raises(SourceResponseError, match="chapter has no manga relationship"):
        MangaDexSource("https://mangadex.org/chapter/chapter-id")


@pytest.mark.parametrize("volumes", [[], None, "", 42])
def test_aggregate_treats_non_object_volumes_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    volumes,
) -> None:
    manga = source()
    monkeypatch.setattr(manga, "_get", lambda url: FakeResponse({"volumes": volumes}))

    assert manga._fetch_aggregate_chapter_count() == 0


def test_aggregate_skips_malformed_volume_and_chapter_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manga = source()
    monkeypatch.setattr(
        manga,
        "_get",
        lambda url: FakeResponse(
            {
                "volumes": {
                    "1": {"chapters": {"1": {}, "2": {}}},
                    "2": {"chapters": []},
                    "3": [],
                    "4": None,
                }
            }
        ),
    )

    assert manga._fetch_aggregate_chapter_count() == 2


def test_metadata_tolerates_optional_fields_with_wrong_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manga = source()
    monkeypatch.setattr(
        manga,
        "_get",
        lambda url: FakeResponse(
            {
                "data": {
                    "attributes": {
                        "availableTranslatedLanguages": {},
                        "title": [{"en": "wrong container"}],
                        "altTitles": {},
                        "description": [],
                        "tags": [
                            None,
                            {"attributes": []},
                            {
                                "attributes": {
                                    "group": "genre",
                                    "name": {"en": "Action"},
                                }
                            },
                        ],
                    },
                    "relationships": [
                        None,
                        {"type": "author", "attributes": []},
                        {
                            "type": "artist",
                            "attributes": {"name": "Example Artist"},
                        },
                        {"type": "cover_art", "attributes": {"fileName": 123}},
                    ],
                }
            }
        ),
    )

    metadata = manga.fetch_metadata()

    assert metadata.title == manga.id
    assert metadata.authors == ["Example Artist"]
    assert metadata.description == ""
    assert metadata.genres == ["Action"]
    assert metadata.cover_art == ""


def test_chapter_feed_skips_malformed_rows_and_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manga = source()
    responses = iter(
        [
            {
                "data": [
                    None,
                    [],
                    {"id": "missing-attributes"},
                    {"id": 123, "attributes": {}},
                    {
                        "id": "valid-id",
                        "attributes": {"chapter": "1", "title": "One"},
                    },
                ],
                "total": "5",
            }
        ]
    )
    monkeypatch.setattr(manga, "_get", lambda url: FakeResponse(next(responses)))

    assert manga._fetch_chapter_feed() == [
        {
            "id": "valid-id",
            "attributes": {"chapter": "1", "title": "One"},
        }
    ]


def test_chapter_list_tolerates_wrong_optional_chapter_field_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manga = source()
    monkeypatch.setattr(
        manga,
        "_fetch_chapter_feed",
        lambda lang_code=None: [
            {
                "id": "chapter-id",
                "attributes": {"volume": {"wrong": "type"}, "chapter": 1, "title": {}},
            }
        ],
    )
    monkeypatch.setattr(manga, "_fetch_aggregate_chapter_count", lambda: 1)

    assert manga.fetch_chapter_list() == [
        BaseChapter(
            "Chapter ",
            "https://mangadex.org/chapter/chapter-id",
            "chapter",
            "",
        )
    ]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"baseUrl": [], "chapter": {"hash": "hash", "data": []}}, "missing baseUrl"),
        ({"baseUrl": "https://img", "chapter": []}, "missing chapter.hash"),
        (
            {"baseUrl": "https://img", "chapter": {"hash": "hash", "data": {}}},
            "missing chapter.data array",
        ),
    ],
)
def test_chapter_images_report_required_shape_errors(
    monkeypatch: pytest.MonkeyPatch,
    payload,
    reason: str,
) -> None:
    manga = source()
    monkeypatch.setattr(manga, "_get", lambda url: FakeResponse(payload))

    with pytest.raises(SourceResponseError, match=reason):
        manga.fetch_chapter_image_list(
            BaseChapter("One", "https://mangadex.org/chapter/chapter-id")
        )


@pytest.mark.parametrize("payload", [[], None, "not JSON object"])
def test_non_object_api_responses_raise_source_error(
    monkeypatch: pytest.MonkeyPatch,
    payload,
) -> None:
    manga = source()
    monkeypatch.setattr(manga, "_get", lambda url: FakeResponse(payload))

    with pytest.raises(SourceResponseError, match="response body is not an object"):
        manga._fetch_aggregate_chapter_count()


def test_invalid_json_raises_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manga = source()
    monkeypatch.setattr(manga, "_get", lambda url: FakeResponse(ValueError("bad JSON")))

    with pytest.raises(SourceResponseError, match="invalid JSON"):
        manga._fetch_aggregate_chapter_count()


@skip_in_ci
def test_kaguya_mangadex() -> None:
    return is_source_working(
        "https://mangadex.org/title/37f5cce0-8070-4ada-96e5-fa24b1bd4ff9",
        title="Kaguya-sama wa Kokurasetai: Tensai-tachi no Renai Zunousen",
        authors=["Akasaka Aka"],
        genres=[
            "Romance",
            "Comedy",
            "Drama",
            "School Life",
            "Slice of Life",
        ],
        description=DESCRIPTION,
        cover_art="https://uploads.mangadex.org/covers/37f5cce0-8070-4ada-96e5-fa24b1bd4ff9/e21ca520-5054-4041-a07e-de8b7c683522.jpg",
    )

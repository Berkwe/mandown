import pytest
from common import skip_in_ci

import mandown
from mandown.base import BaseChapter
from mandown.errors import SourceResponseError
from mandown.sources import get_class_for
from mandown.sources.source_naver import NaverWebtoonSource
from mandown.sources.source_naver_series import NaverSeriesSource

DETAIL_HTML = """
<html>
  <head>
    <meta property="og:image" content="https://example.com/series-cover.jpg">
  </head>
  <body>
    <div class="end_head"><h2>무사만리행[독점]</h2></div>
    <ul class="end_info">
      <li><span><a href="?categoryTypeCode=genre">소년</a></span></li>
      <li><span>글</span><a href="/search/search.series?q=writer">운(雲)</a></li>
      <li><span>그림</span><a href="/search/search.series?q=artist">배민기</a></li>
    </ul>
    <div class="_synopsis">Short...</div>
    <div class="_synopsis">Full description<span>접기</span></div>
    <h5 class="end_total_episode">총 <strong>289</strong>화</h5>
  </body>
</html>
"""


class FakeResponse:
    def __init__(self, *, text: str = "", data: dict | None = None) -> None:
        self.text = text
        self._data = data or {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def responses(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls.append((url, kwargs))
        if url == "https://series.naver.com/comic/detail.series?productNo=5173965":
            return FakeResponse(text=DETAIL_HTML)
        if url == "https://comic.naver.com/api/search/all":
            return FakeResponse(
                data={
                    "searchWebtoonResult": {
                        "searchViewList": [
                            {
                                "titleId": 746857,
                                "titleName": "무사만리행",
                                "articleTotalCount": 289,
                                "communityArtists": [
                                    {"name": "운"},
                                    {"name": "배민기"},
                                ],
                            }
                        ]
                    }
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("mandown.sources.source_naver_series.requests.get", fake_get)
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "https://series.naver.com/comic/detail.nhn?productNo=5173965",
        "https://series.naver.com/comic/detail.series?productNo=5173965",
        "https://m.series.naver.com/comic/detail.series?productNo=5173965",
    ],
)
def test_series_urls_are_registered(url: str) -> None:
    assert get_class_for(url) is NaverSeriesSource


def test_series_metadata_and_chapters_use_matching_webtoon_mirror(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[tuple[str, dict]],
) -> None:
    chapters = [
        BaseChapter(
            "1화",
            "https://m.comic.naver.com/webtoon/detail?titleId=746857&no=1",
            chapter_number="1",
        )
    ]
    monkeypatch.setattr(
        NaverWebtoonSource,
        "fetch_chapter_list",
        lambda self: chapters,
    )
    monkeypatch.setattr(
        NaverWebtoonSource,
        "fetch_chapter_image_list",
        lambda self, chapter: ["https://example.com/page-1.jpg"],
    )

    source = NaverSeriesSource("https://series.naver.com/comic/detail.nhn?productNo=5173965")

    assert source.fetch_metadata().asdict() == {
        "title": "무사만리행[독점]",
        "authors": ["운(雲)", "배민기"],
        "url": "https://series.naver.com/comic/detail.series?productNo=5173965",
        "genres": ["소년"],
        "description": "Full description",
        "cover_art": "https://example.com/series-cover.jpg",
    }
    assert source.fetch_chapter_list() == chapters
    assert source.fetch_chapter_image_list(chapters[0]) == ["https://example.com/page-1.jpg"]
    assert source._webtoon_source is not None
    assert source._webtoon_source.title_id == "746857"
    assert responses[1][1]["params"] == {"keyword": "무사만리행"}
    assert len(responses) == 2


def test_series_rejects_drm_only_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "detail.series" in url:
            return FakeResponse(text=DETAIL_HTML.replace("289", "7"))
        return FakeResponse(data={"searchWebtoonResult": {"searchViewList": []}})

    monkeypatch.setattr("mandown.sources.source_naver_series.requests.get", fake_get)

    with pytest.raises(
        SourceResponseError,
        match="no downloadable Naver Webtoon mirror.*authenticated license",
    ):
        NaverSeriesSource(
            "https://series.naver.com/comic/detail.series?productNo=123"
        ).fetch_metadata()


def test_series_does_not_choose_same_title_with_different_chapter_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "detail.series" in url:
            return FakeResponse(text=DETAIL_HTML)
        return FakeResponse(
            data={
                "searchWebtoonResult": {
                    "searchViewList": [
                        {
                            "titleId": 1,
                            "titleName": "무사만리행",
                            "articleTotalCount": 100,
                            "communityArtists": [
                                {"name": "운"},
                                {"name": "배민기"},
                            ],
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("mandown.sources.source_naver_series.requests.get", fake_get)

    with pytest.raises(SourceResponseError, match="no downloadable"):
        NaverSeriesSource(
            "https://series.naver.com/comic/detail.series?productNo=5173965"
        ).fetch_chapter_list()


@skip_in_ci
def test_musa_mallihaeng_series_live() -> None:
    comic = mandown.query("https://series.naver.com/comic/detail.nhn?productNo=5173965")

    assert comic.metadata.title == "무사만리행[독점]"
    assert comic.metadata.url == ("https://series.naver.com/comic/detail.series?productNo=5173965")
    assert len(comic.chapters) >= 284
    assert comic.chapters[0].chapter_number == "1"
    assert comic.get_chapter_image_urls(comic.chapters[0])

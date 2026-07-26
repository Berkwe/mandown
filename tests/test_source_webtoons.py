import pytest
import requests
from common import is_source_working, skip_in_ci

from mandown.errors import SourceResponseError
from mandown.sources.source_webtoons import WebtoonsSource

BATMAN_DESCRIPTION = "Batman needs a break. But with new vigilante Duke Thomas moving into Wayne Manor and an endless supply of adopted, fostered, and biological superhero children to manage, Bruce Wayne is going to have his hands full. Being a father can't be harder than being Batman, right?"

REYN_DESCRIPTION = "Betrayal, hidden identities, family secrets. Left alone after her mother's murder, Reyn struggles to accept her wings, and searches for truth in a world that wants her dead. But the more she discovers, the more she begins to fear herself. Her brother has all the answers, but he disappears that same night. Can Reyn find him before the world discovers who she really is?  ~UP every Saturday 11AM EST~"


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content_type: str,
        text: str = "",
        data: object = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = text
        self._data = data

    def json(self) -> object:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} response")


@pytest.fixture
def webtoons_source() -> WebtoonsSource:
    return WebtoonsSource(
        "https://www.webtoons.com/en/romance/example/list?title_no=7688"
    )


def metadata_html(*, include_title: bool = True, include_author: bool = True) -> str:
    title = '<meta property="og:title" content="Example">' if include_title else ""
    author = (
        '<meta property="com-linewebtoon:webtoon:author" content="Author">'
        if include_author
        else ""
    )
    return f"""
    <html><head>
      {title}
      {author}
      <meta property="og:image" content="https://example.com/cover.jpg">
    </head><body>
      <div id="content">
        <div class="summary">Description</div>
      </div>
    </body></html>
    """


@pytest.mark.parametrize(
    ("html", "missing_field"),
    [
        (metadata_html(include_title=False), "og:title meta content"),
        (metadata_html(include_author=False), "author meta content"),
    ],
)
def test_metadata_reports_missing_required_meta(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
    html: str,
    missing_field: str,
) -> None:
    monkeypatch.setattr(
        "mandown.sources.source_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(content_type="text/html", text=html),
    )

    with pytest.raises(
        SourceResponseError,
        match=rf"Webtoons response error:.*status=200.*missing {missing_field}",
    ):
        webtoons_source.fetch_metadata()


def test_chapters_reports_null_result(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
) -> None:
    monkeypatch.setattr(
        "mandown.sources.source_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(
            content_type="application/json",
            data={"result": None},
        ),
    )

    with pytest.raises(SourceResponseError, match="status=200.*missing result object"):
        webtoons_source.fetch_chapter_list()


def test_chapters_reports_empty_episode_list(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
) -> None:
    monkeypatch.setattr(
        "mandown.sources.source_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(
            content_type="application/json",
            data={"result": {"episodeList": []}},
        ),
    )

    with pytest.raises(SourceResponseError, match="status=200.*episodeList is empty"):
        webtoons_source.fetch_chapter_list()


def test_chapters_reports_unexpected_content_type(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
) -> None:
    monkeypatch.setattr(
        "mandown.sources.source_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(
            content_type="text/html",
            text="<html>rate limited</html>",
        ),
    )

    with pytest.raises(
        SourceResponseError,
        match="status=200.*unexpected Content-Type 'text/html'",
    ):
        webtoons_source.fetch_chapter_list()


@pytest.mark.parametrize("transient_status", [429, 503])
def test_chapters_retries_transient_http_status(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
    transient_status: int,
) -> None:
    responses = [
        FakeResponse(content_type="application/json", status_code=transient_status),
        FakeResponse(content_type="application/json", status_code=transient_status),
        FakeResponse(
            content_type="application/json",
            data={
                "result": {
                    "episodeList": [
                        {
                            "episodeNo": 7,
                            "episodeTitle": "Episode 7",
                            "viewerLink": "/en/example/episode-7/viewer?episode_no=7",
                        }
                    ]
                }
            },
        ),
    ]
    calls: list[str] = []

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls.append(url)
        assert kwargs["timeout"] == 20
        return responses.pop(0)

    monkeypatch.setattr("mandown.sources.source_webtoons.requests.get", fake_get)
    monkeypatch.setattr("mandown.sources.source_webtoons.time.sleep", lambda delay: None)

    chapters = webtoons_source.fetch_chapter_list()

    assert len(calls) == 3
    assert chapters[0].chapter_number == "7"


def test_chapters_does_not_retry_non_transient_http_status(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
) -> None:
    calls = 0

    def fake_get(url: str, **kwargs) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse(content_type="application/json", status_code=404)

    monkeypatch.setattr("mandown.sources.source_webtoons.requests.get", fake_get)

    with pytest.raises(SourceResponseError, match="status=404.*HTTP request failed"):
        webtoons_source.fetch_chapter_list()

    assert calls == 1


def test_chapter_number_falls_back_to_list_order(
    monkeypatch: pytest.MonkeyPatch,
    webtoons_source: WebtoonsSource,
) -> None:
    monkeypatch.setattr(
        "mandown.sources.source_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(
            content_type="application/json",
            data={
                "result": {
                    "episodeList": [
                        {
                            "episodeTitle": "Prologue",
                            "viewerLink": "/en/example/prologue/viewer",
                        }
                    ]
                }
            },
        ),
    )

    assert webtoons_source.fetch_chapter_list()[0].chapter_number == "1"


@skip_in_ci
def test_batman() -> None:
    return is_source_working(
        "https://www.webtoons.com/en/slice-of-life/batman-wayne-family-adventures/list?title_no=3180",
        title="Batman: Wayne Family Adventures",
        authors=["StarBite", "CRC Payne"],
        genres=["Slice of Life", "Ability User", "Family Drama"],
        description=BATMAN_DESCRIPTION,
        cover_art="https://webtoon-phinf.pstatic.net/20250205_19/1738718670675e8Srk_JPEG/3180.jpg",
    )


@skip_in_ci
def test_reyn() -> None:
    # test canvas
    return is_source_working(
        "https://www.webtoons.com/en/canvas/reyn-angel-of-freedom/list?title_no=423104",
        title="Reyn: Angel of Freedom",
        authors=["erlance"],
        genres=["Supernatural"],
        description=REYN_DESCRIPTION,
        cover_art="https://webtoon-phinf.pstatic.net/20230703_36/1688394256514spH74_JPEG/20f96dd9-008b-4fe9-bf68-4b6ed37e49f12435456437735264238.jpeg",
    )

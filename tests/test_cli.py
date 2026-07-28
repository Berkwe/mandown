import sys

import pytest
from common import skip_in_ci
from typer import BadParameter, Exit

from mandown import (
    AniListMangaSummary,
    AniListPageInfo,
    AniListSearchResponse,
    AniListTitle,
    BaseComic,
    __version_str__,
    cli,
)


def assert_expected_output(capsys, input: str, output: str) -> None:
    sys.argv = input.split()

    with pytest.raises((Exit, SystemExit)):
        cli.main()

    captured = capsys.readouterr()
    assert output in captured.out


@skip_in_ci
def test_cli_query(capsys) -> None:
    url = "https://www.webtoons.com/en/slice-of-life/batman-wayne-family-adventures/list?title_no=3180"

    res = cli.cli_query(url)
    assert isinstance(res, BaseComic)

    captured = capsys.readouterr()
    assert f"Searching sources for {url}" in captured.out


def test_invalid_cli_query(capsys) -> None:
    with pytest.raises(Exit):
        cli.cli_query("invalid")

    captured = capsys.readouterr()
    assert "Could not match URL with available sources" in captured.out


def test_callbacks(capsys) -> None:
    assert_expected_output(capsys, "mandown -v", f"mandown {__version_str__}")
    assert_expected_output(capsys, "mandown --version", f"mandown {__version_str__}")

    assert_expected_output(capsys, "mandown --help", "Usage: mandown [OPTIONS] COMMAND")
    assert_expected_output(capsys, "mandown", "Usage: mandown [OPTIONS] COMMAND")

    assert_expected_output(capsys, "mandown --supported-sites", "Webtoons: https://webtoons.com")

    assert_expected_output(capsys, "mandown -l", " - Kobo Sage: 'sage'")


@pytest.mark.parametrize("limit", [0, 26])
def test_search_command_enforces_mandown_cli_limit(limit: int) -> None:
    with pytest.raises(BadParameter, match="Mandown's CLI performance limit"):
        cli.search_command("Solo", limit=limit)


@pytest.mark.parametrize("limit", [1, 25])
def test_search_command_accepts_product_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    limit: int,
) -> None:
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def search_manga(self, query: str, **kwargs):
            calls.append((query, kwargs))
            return AniListSearchResponse(
                items=(
                    AniListMangaSummary(
                        id=105398,
                        title=AniListTitle(english="Solo Leveling"),
                        site_url="https://anilist.co/manga/105398",
                    ),
                ),
                page_info=AniListPageInfo(current_page=1),
            )

    monkeypatch.setattr(cli, "AniListClient", FakeClient)

    cli.search_command(
        "Solo",
        page=1,
        limit=limit,
        details=False,
        external_links=False,
    )

    assert calls[0][1]["per_page"] == limit
    assert "[105398] Solo Leveling" in capsys.readouterr().out

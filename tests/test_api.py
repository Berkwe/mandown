from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

import mandown
from mandown import io
from mandown import BaseChapter, BaseComic, BaseMetadata


def test_load_save(tmp_path: Path) -> None:
    comic = BaseComic(
        BaseMetadata(
            title="Test Comic",
            authors=["Test Author"],
            url="",
            genres=["Test", "Genres"],
            description="Test Description",
            cover_art="https://example.com/cover.jpg",
        ),
        [
            BaseChapter("Test Chapter", "https://example.com/chapter"),
        ],
    )
    mandown.save_metadata(comic, tmp_path)
    loaded = mandown.load(tmp_path)
    assert comic.asdict() == loaded.asdict()


def test_resize_images_to_panel_size_uses_height_width(tmp_path: Path) -> None:
    image_path = tmp_path / "panel.jpg"
    Image.new("RGB", (400, 200), "red").save(image_path)

    io.resize_image_to_panel_size(image_path, (120, 80))

    with Image.open(image_path) as image:
        assert image.size == (80, 120)


def test_split_image_to_panel_size_keeps_all_vertical_content(tmp_path: Path) -> None:
    image_path = tmp_path / "long-page.jpg"
    dest_path = tmp_path / "panels"
    dest_path.mkdir()
    Image.new("RGB", (690, 5000), "white").save(image_path)

    next_index = io.split_image_to_panel_size(
        image_path,
        dest_path,
        start_index=1,
        panel_size=(1200, 800),
    )

    panels = sorted(dest_path.iterdir())
    assert next_index == 6
    assert [panel.name for panel in panels] == [
        "00001.jpg",
        "00002.jpg",
        "00003.jpg",
        "00004.jpg",
        "00005.jpg",
    ]
    for panel in panels:
        with Image.open(panel) as image:
            assert image.size == (800, 1200)


def test_async_download_image_resizes_before_download_job_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_image = tmp_path / "source.jpg"
    Image.new("RGB", (400, 200), "red").save(source_image)

    def fake_get(url: str, headers: dict[str, str] | None, timeout: int) -> SimpleNamespace:
        return SimpleNamespace(status_code=200, content=source_image.read_bytes())

    monkeypatch.setattr(io.RealRequests, "get", fake_get)

    io.async_download_image(
        ("https://example.com/panel.jpg", tmp_path, "00001.jpg", None, (120, 80))
    )

    with Image.open(tmp_path / "00001.jpg") as image:
        assert image.size == (80, 120)


def test_chapter_number_range_does_not_use_list_index() -> None:
    comic = BaseComic(
        BaseMetadata("Test Comic", [], "", [], "", ""),
        [
            BaseChapter("Chapter 29", "https://example.com/29", chapter_number="29"),
            BaseChapter("Chapter 30", "https://example.com/30", chapter_number="30"),
            BaseChapter("Chapter 160", "https://example.com/160", chapter_number="160"),
        ],
    )

    with pytest.raises(ValueError, match="Chapter 1 was not found"):
        comic.set_chapter_range(start=1, end=2, by_chapter_number=True)

    comic.set_chapter_range(start=29, end=30, by_chapter_number=True)
    assert [chapter.chapter_number for chapter in comic.chapters] == ["29", "30"]

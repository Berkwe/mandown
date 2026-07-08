from pathlib import Path

from PIL import Image

import mandown
from mandown import BaseChapter, BaseComic, BaseMetadata
from mandown.api import _resize_images_to_panel_size


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

    _resize_images_to_panel_size([image_path], (120, 80))

    with Image.open(image_path) as image:
        assert image.size == (80, 120)

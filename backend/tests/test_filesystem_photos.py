"""Tests for FilesystemPhotosAPI — path-traversal protection and basic operations."""
from pathlib import Path

import pytest

from core.filesystem_photos import FilesystemPhotosAPI


@pytest.fixture
def photo_base(tmp_path: Path) -> Path:
    """Minimal album layout: base/Album A/photo.jpg (empty file)."""
    album = tmp_path / "Album A"
    album.mkdir()
    (album / "photo.jpg").write_bytes(b"not-a-real-jpeg")
    (album / "video.mp4").write_bytes(b"x")
    (album / "readme.txt").write_text("ignore me")  # not a media file
    # Hidden dir must not surface as an album
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "photo.jpg").write_bytes(b"x")
    return tmp_path


def test_list_folders_skips_hidden(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    names = [f["name"] for f in api.list_folders()]
    assert "Album A" in names
    assert ".hidden" not in names


def test_list_items_photo_filter(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    items = api.list_items("Album A", item_type="photo")
    filenames = [i["filename"] for i in items]
    assert "photo.jpg" in filenames
    assert "video.mp4" not in filenames
    assert "readme.txt" not in filenames


def test_list_items_excludes_non_media(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    items = api.list_items("Album A")  # all types
    filenames = [i["filename"] for i in items]
    assert "readme.txt" not in filenames


def test_path_traversal_folder_blocked(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    # "../" must not escape the base directory
    assert api.list_items("../secret") == []


def test_path_traversal_file_blocked(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    with pytest.raises(FileNotFoundError):
        api.get_thumbnail("../outside.jpg", cache_key="", size="m")


def test_cache_path_uses_hash(photo_base):
    api = FilesystemPhotosAPI(photo_base)
    src = photo_base / "Album A" / "photo.jpg"
    p1 = api._cache_path(src, "m")
    p2 = api._cache_path(src, "sm")
    # Unterschiedliche Size → unterschiedlicher Cache-Pfad
    assert p1 != p2
    # Selbe Inputs → deterministisch
    assert p1 == api._cache_path(src, "m")

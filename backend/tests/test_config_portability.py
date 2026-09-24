"""SPK-Portabilität (09/2026): MPD_HOMES_ROOT / MPD_PERSONAL_SUBDIR /
MPD_STATE_DIR — Defaults = heutiges Verhalten, Overrides greifen."""
from pathlib import Path

import config
from core.library_warmer import discover_personal_spaces
from tests.conftest import make_jpeg


def test_default_personal_path_defaults():
    assert config.default_personal_path("anna") == "/volume1/homes/anna/Photos"


def test_default_personal_path_override(monkeypatch):
    monkeypatch.setattr(config, "HOMES_ROOT", Path("/volume2/homes"))
    monkeypatch.setattr(config, "PERSONAL_SUBDIR", "Bilder")
    assert config.default_personal_path("anna") == "/volume2/homes/anna/Bilder"


def test_discover_personal_spaces_honors_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSONAL_SUBDIR", "Bilder")
    album = tmp_path / "anna" / "Bilder" / "2020 - Test"
    make_jpeg(album / "p1.jpg")
    (album / "album.json").write_text("{}", encoding="utf-8")
    # Nachbar mit dem alten Standard-Unterordner wird NICHT gefunden
    other = tmp_path / "bert" / "Photos" / "2020 - Test"
    make_jpeg(other / "p1.jpg")
    (other / "album.json").write_text("{}", encoding="utf-8")

    found = discover_personal_spaces(tmp_path)
    assert found == [("anna", tmp_path / "anna" / "Bilder")]


def test_state_dir_default_is_backend_dir():
    assert config.STATE_DIR == Path(config.__file__).parent
    assert config.STATE_DIR.is_dir()

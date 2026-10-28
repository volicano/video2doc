from __future__ import annotations

from pathlib import Path

import pytest

from video2doc.utils import files
from video2doc.utils.files import atomic_write_text


def test_atomic_write_text_replaces_and_cleans_temp(tmp_path: Path) -> None:
    path = tmp_path / "job.json"
    path.write_text("old", encoding="utf-8")

    atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(".job.json.*")) == []


def test_atomic_write_text_keeps_old_file_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "job.json"
    path.write_text("old", encoding="utf-8")

    def fail_replace(source, target) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".job.json.*")) == []

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from video2doc.config import Config, WriteConfig, load_config
from video2doc.errors import ConfigError


def test_defaults() -> None:
    config = Config()
    assert config.asr.provider == "faster-whisper"
    assert config.asr.language == "zh"
    assert config.video.prefer_subtitles is True
    assert config.render.format == "html-single"
    assert config.write.enabled is True
    assert config.write.max_sections == 12


def test_example_config_is_valid(tmp_path: Path) -> None:
    from video2doc.config import EXAMPLE_CONFIG

    path = tmp_path / "video2doc.toml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    config = load_config(path)
    assert config.write.enabled is True
    assert config.render.show_toc is True
    assert config.llm.max_tokens >= 1024


def test_chunk_overlap_is_bounded() -> None:
    # 重叠超过窗口一半会让步长退化成 1，长视频会发出大量几乎相同的请求。
    with pytest.raises(ValidationError):
        WriteConfig(chunk_utterances=40, chunk_overlap=21)
    with pytest.raises(ValidationError):
        WriteConfig(chunk_utterances=0, chunk_overlap=0)
    with pytest.raises(ValidationError):
        WriteConfig(chunk_utterances=40, chunk_overlap=-1)

    assert WriteConfig(chunk_utterances=40, chunk_overlap=20).chunk_overlap == 20


def test_load_toml_overrides(tmp_path: Path) -> None:
    path = tmp_path / "video2doc.toml"
    path.write_text(
        """
[asr]
provider = "openai"
model = "whisper-1"

[frames]
max_frames = 12
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.asr.provider == "openai"
    assert config.asr.model == "whisper-1"
    assert config.frames.max_frames == 12
    # 未覆盖的字段保留默认值
    assert config.video.max_height == 1080


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "video2doc.toml"
    path.write_text('[asr]\nprovder = "openai"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "asr.provder" in str(excinfo.value)


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    # Windows 记事本保存的 UTF-8 默认带 BOM，不该因此报「语法错误」
    path = tmp_path / "video2doc.toml"
    path.write_bytes(b"\xef\xbb\xbf[frames]\nmax_frames = 7\n")
    assert load_config(path).frames.max_frames == 7


def test_non_utf8_config_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "video2doc.toml"
    path.write_bytes("# 中文注释\n[frames]\nmax_frames = 7\n".encode("gbk"))
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "UTF-8" in str(excinfo.value)


def test_missing_explicit_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_config_hash_changes_with_content() -> None:
    a = Config()
    b = Config()
    assert a.hash == b.hash
    b.asr.model = "small"
    assert a.hash != b.hash

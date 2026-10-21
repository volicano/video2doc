from __future__ import annotations

from pathlib import Path

import pytest

from video2doc.schema import SubtitleFile
from video2doc.subtitles import (
    parse_subtitle_file,
    parse_subtitle_text,
    parse_timestamp,
    pick_subtitle,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:01:02.500", 62.5),
        ("00:01:02,500", 62.5),
        ("01:02.500", 62.5),
        ("62.5", 62.5),
        ("00:00:00.000", 0.0),
        ("broken", -1.0),
    ],
)
def test_parse_timestamp(raw: str, expected: float) -> None:
    assert parse_timestamp(raw) == pytest.approx(expected)


def test_parse_vtt_basic() -> None:
    text = """WEBVTT
Kind: captions
Language: zh

00:00:01.000 --> 00:00:04.000
大家好，今天讲闭包

00:00:04.800 --> 00:00:08.000
先看一段代码
"""
    cues = parse_subtitle_text(text)
    assert [cue.text for cue in cues] == ["大家好，今天讲闭包", "先看一段代码"]
    assert cues[0].start == pytest.approx(1.0)
    assert cues[1].end == pytest.approx(8.0)


def test_parse_srt_with_index_lines() -> None:
    text = """1
00:00:01,000 --> 00:00:03,000
第一句

2
00:00:03,500 --> 00:00:05,000
第二句
"""
    cues = parse_subtitle_text(text)
    assert [cue.text for cue in cues] == ["第一句", "第二句"]


def test_rolling_captions_are_deduplicated() -> None:
    """YouTube 自动字幕会把上一句带上，直接拼接会重复两遍。"""
    text = """WEBVTT

00:00:01.000 --> 00:00:03.000
大家好

00:00:03.000 --> 00:00:05.000
大家好
今天讲闭包

00:00:05.000 --> 00:00:07.000
今天讲闭包
闭包是函数和词法环境的组合
"""
    cues = parse_subtitle_text(text, merge_gap=0.0)
    joined = " ".join(cue.text for cue in cues)
    assert joined.count("大家好") == 1
    assert "闭包是函数和词法环境的组合" in joined


def test_tags_and_entities_are_stripped() -> None:
    text = """WEBVTT

00:00:01.000 --> 00:00:03.000
<v 讲师>时间复杂度是 <c>O(n log n)</c>&nbsp;级别</v>
"""
    cues = parse_subtitle_text(text)
    assert cues[0].text == "时间复杂度是 O(n log n) 级别"
    assert cues[0].speaker == "讲师"


def test_noise_cues_are_dropped() -> None:
    text = """WEBVTT

00:00:01.000 --> 00:00:03.000
[音乐]

00:00:03.000 --> 00:00:05.000
开始讲
"""
    cues = parse_subtitle_text(text)
    assert [cue.text for cue in cues] == ["开始讲"]


def test_adjacent_short_cues_are_merged() -> None:
    text = """WEBVTT

00:00:01.000 --> 00:00:02.000
那么

00:00:02.100 --> 00:00:03.000
闭包是什么
"""
    cues = parse_subtitle_text(text, merge_gap=0.3)
    assert len(cues) == 1
    assert cues[0].text == "那么 闭包是什么"
    assert cues[0].end == pytest.approx(3.0)


def test_parse_gbk_encoded_file(tmp_path: Path) -> None:
    path = tmp_path / "video.zh.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n中文编码测试\n".encode("gb18030"))
    cues = parse_subtitle_file(path)
    assert cues[0].text == "中文编码测试"


def test_pick_subtitle_prefers_manual_and_language() -> None:
    subs = [
        SubtitleFile(path="a.vtt", language="en", auto=True),
        SubtitleFile(path="b.vtt", language="zh", auto=True),
        SubtitleFile(path="c.vtt", language="zh-Hans", auto=False),
    ]
    chosen = pick_subtitle(subs, "zh")
    assert chosen is not None
    assert chosen.path == "c.vtt"


def test_pick_subtitle_returns_none_for_unknown_language() -> None:
    subs = [SubtitleFile(path="a.vtt", language="ja")]
    assert pick_subtitle(subs, "zh") is None

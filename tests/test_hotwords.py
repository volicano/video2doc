from __future__ import annotations

from video2doc.hotwords import auto_hotwords, build_initial_prompt, resolve_hotwords
from video2doc.schema import SourceInfo


def _source(**kwargs) -> SourceInfo:
    data = {"kind": "url", "path": "x.mp4", "title": ""}
    data.update(kwargs)
    return SourceInfo(**data)


def test_hotwords_from_tags_and_title() -> None:
    source = _source(
        title="Java 面试必备：AQS 与 CAS 原理",
        tags=["并发编程", "JVM"],
    )
    words = auto_hotwords(source)
    assert "并发编程" in words
    assert "AQS" in words
    assert "CAS" in words


def test_generic_words_are_filtered() -> None:
    source = _source(title="视频教程：全集完整版", tags=["教程"])
    words = auto_hotwords(source)
    assert "教程" not in words
    assert "视频" not in words
    assert "全集" not in words


def test_dedup_is_case_insensitive() -> None:
    source = _source(title="CAS cas", tags=["cas"])
    words = auto_hotwords(source)
    assert len([w for w in words if w.lower() == "cas"]) == 1


def test_resolve_puts_user_words_first() -> None:
    source = _source(title="Redis 缓存穿透", tags=["缓存"])
    words = resolve_hotwords(["布隆过滤器"], True, source)
    assert words[0] == "布隆过滤器"
    assert "缓存" in words


def test_limit_is_respected() -> None:
    source = _source(title=" ".join(f"Term{i}X" for i in range(100)))
    assert len(auto_hotwords(source, limit=5)) == 5


def test_build_initial_prompt() -> None:
    prompt = build_initial_prompt(["闭包", "作用域"], "zh")
    assert prompt is not None
    assert "闭包" in prompt and "作用域" in prompt
    assert build_initial_prompt([], "zh", "额外背景") is not None
    assert "额外背景" in build_initial_prompt([], "zh", "额外背景")

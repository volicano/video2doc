from __future__ import annotations

from video2doc.job import derive_job_id


def test_bilibili_bv() -> None:
    assert derive_job_id("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD"


def test_bilibili_av() -> None:
    assert derive_job_id("https://www.bilibili.com/video/av170001") == "av170001"


def test_youtube() -> None:
    assert derive_job_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert derive_job_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_local_file_uses_stem() -> None:
    assert derive_job_id(r"D:\videos\Java 面试 01.mp4") == "Java-面试-01"


def test_odd_target_still_produces_id() -> None:
    assert derive_job_id("!!!") != ""

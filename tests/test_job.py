from __future__ import annotations

import os

from video2doc.job import derive_job_id


def test_bilibili_bv() -> None:
    assert derive_job_id("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD"


def test_bilibili_av() -> None:
    assert derive_job_id("https://www.bilibili.com/video/av170001") == "av170001"


def test_youtube() -> None:
    assert derive_job_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert derive_job_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_local_file_uses_stem(tmp_path) -> None:
    # 用真实路径构造，避免把 Windows 风格路径写死在测试里（Linux 上反斜杠不是分隔符）
    target = os.path.join(str(tmp_path), "Java 面试 01.mp4")
    assert derive_job_id(target) == "Java-面试-01"


def test_odd_target_still_produces_id() -> None:
    assert derive_job_id("!!!") != ""

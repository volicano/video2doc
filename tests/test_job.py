from __future__ import annotations

import os

import pytest

from video2doc.config import Config
from video2doc.errors import StageError
from video2doc.job import derive_job_id, open_job, read_job, target_identity
from video2doc.schema import Job


def test_bilibili_bv_includes_part() -> None:
    assert derive_job_id("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD-p2"
    assert derive_job_id("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == "BV1xx411c7mD"


def test_bilibili_av() -> None:
    assert derive_job_id("https://www.bilibili.com/video/av170001") == "av170001"


def test_youtube() -> None:
    assert derive_job_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert derive_job_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_douyin_long_link_and_share_page() -> None:
    video_id = "7298145681699622182"
    assert derive_job_id(f"https://www.douyin.com/video/{video_id}") == video_id
    assert derive_job_id(f"https://www.iesdouyin.com/share/video/{video_id}/") == video_id
    assert derive_job_id(f"https://www.douyin.com/discover?modal_id={video_id}") == video_id


def test_douyin_short_link_uses_code() -> None:
    assert derive_job_id("复制打开抖音 https://v.douyin.com/iRNBho6u/ 看看") == "douyin-iRNBho6u"


def test_local_file_uses_stem_and_path_digest(tmp_path) -> None:
    # 用真实路径构造，避免把 Windows 风格路径写死在测试里（Linux 上反斜杠不是分隔符）
    target = os.path.join(str(tmp_path), "Java 面试 01.mp4")
    job_id = derive_job_id(target)
    assert job_id.startswith("local-Java-面试-01-")
    assert derive_job_id(target) == job_id

    other = os.path.join(str(tmp_path / "other"), "Java 面试 01.mp4")
    assert derive_job_id(other) != job_id


def test_odd_target_still_produces_stable_id() -> None:
    first = derive_job_id("!!!")
    assert first.startswith("local-video-")
    assert derive_job_id("!!!") == first


def test_identity_ignores_share_text_and_tracking_parameters() -> None:
    assert target_identity("复制打开抖音 https://v.douyin.com/iRNBho6u/ 看看") == target_identity(
        "https://v.douyin.com/iRNBho6u/"
    )
    assert target_identity("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42") == target_identity(
        "https://youtu.be/dQw4w9WgXcQ"
    )


def test_bilibili_parts_are_distinct_targets() -> None:
    assert target_identity("https://www.bilibili.com/video/BV1xx411c7mD?p=1") != target_identity(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    )


def test_relative_and_absolute_local_paths_share_identity(tmp_path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    monkeypatch.chdir(tmp_path)

    assert target_identity("clip.mp4") == target_identity(str(video))
    assert target_identity("clip.mp4").startswith("file:")


def test_open_job_reuses_normalized_target(tmp_path) -> None:
    job_dir = tmp_path / "job"
    first, created = open_job(
        job_dir,
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        Config(),
    )
    assert created is True
    assert first.target_identity == target_identity("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    again, created = open_job(
        job_dir,
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        Config(),
    )
    assert created is False
    assert again.job_id == first.job_id


def test_open_job_rejects_different_target(tmp_path) -> None:
    job_dir = tmp_path / "job"
    open_job(job_dir, "https://youtu.be/dQw4w9WgXcQ", Config())

    with pytest.raises(StageError, match="另一个视频"):
        open_job(job_dir, "https://youtu.be/aaaaaaaaaaa", Config())


def test_open_job_migrates_old_job_without_identity(tmp_path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    old = Job(
        job_id="job",
        target="https://youtu.be/dQw4w9WgXcQ",
        created_at="2026-10-01T00:00:00",
        updated_at="2026-10-01T00:00:00",
    )
    (job_dir / "job.json").write_text(old.model_dump_json(), encoding="utf-8")

    job, created = open_job(
        job_dir,
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
        Config(),
    )

    assert created is False
    assert job.target_identity == target_identity("https://youtu.be/dQw4w9WgXcQ")
    assert read_job(job_dir).target_identity == job.target_identity


def test_non_youtube_v_parameter_does_not_become_a_youtube_id() -> None:
    job_id = derive_job_id("https://example.com/watch?v=dQw4w9WgXcQ")
    assert job_id.startswith("url-")

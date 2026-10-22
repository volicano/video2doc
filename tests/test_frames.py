from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from video2doc.visual.shots import Sample, Shot, build_shots, kind_of, pick_sample, select_shots
from video2doc.visual.stats import (
    FrameStat,
    classify,
    dhash,
    distance,
    edge_score,
    info_score,
    sharpness,
)


def _samples(edges: list[float], *, start: float = 0.0, interval: float = 1.0) -> list[Sample]:
    return [
        Sample(
            index=index,
            time=start + index * interval,
            stat=FrameStat(edge=edge, skin=0.0, brightness=0.6),
        )
        for index, edge in enumerate(edges)
    ]


def _shot(start: float, edge: float, *, count: int = 3) -> Shot:
    samples = [
        Sample(
            index=index,
            time=start + index,
            stat=FrameStat(edge=edge, skin=0.0, brightness=0.6),
        )
        for index in range(count)
    ]
    return Shot(start=start, end=start + count, samples=samples)


# ---------------------------------------------------------------- 镜头切分


def test_build_shots_splits_on_change() -> None:
    samples = _samples([0.2] * 10)
    samples[5].stat.diff = 0.3

    shots = build_shots(
        samples, duration=10.0, interval=1.0, change_threshold=0.1, min_shot_sec=1.0
    )

    assert len(shots) == 2
    assert shots[0].start == 0.0
    assert shots[0].end == 5.0
    assert shots[1].start == 5.0
    assert shots[1].end == 10.0


def test_build_shots_merges_too_short() -> None:
    samples = _samples([0.2] * 4)
    samples[1].stat.diff = 0.5  # 只隔 1 秒，会被合并回前一个镜头

    shots = build_shots(samples, duration=4.0, interval=1.0, change_threshold=0.1, min_shot_sec=1.5)

    assert len(shots) == 1
    assert shots[0].end == 4.0


def test_build_shots_clamps_to_duration() -> None:
    samples = _samples([0.2] * 3)
    shots = build_shots(samples, duration=2.5, interval=1.0, min_shot_sec=0.1)
    assert shots[-1].end == 2.5


def test_build_shots_splits_when_hash_changes() -> None:
    # 白底换白底：像素差分很小（0.01），但版面完全不同
    samples = _samples([0.2] * 6)
    for sample in samples:
        sample.stat.diff = 0.01
    for sample in samples[:3]:
        sample.hash = 1
    for sample in samples[3:]:
        sample.hash = 0xFFF0  # 汉明距离 12

    shots = build_shots(
        samples,
        duration=6.0,
        interval=1.0,
        change_threshold=0.1,
        hash_threshold=12,
        min_shot_sec=1.0,
    )

    assert len(shots) == 2
    assert shots[1].start == 3.0


# ------------------------------------------------------------------ 选帧


def test_pick_sample_prefers_dense_tail() -> None:
    shot = _shot(0.0, 0.1, count=4)
    shot.samples[2].stat.edge = 0.9
    assert pick_sample(shot).index == 2


def test_select_shots_respects_budget_and_gap() -> None:
    shots = [_shot(start=index * 10.0, edge=0.3 - index * 0.01) for index in range(10)]
    chosen = select_shots(
        shots,
        duration=100.0,
        max_frames=3,
        min_gap_sec=20.0,
        coverage_buckets=4,
    )

    assert len(chosen) == 3
    times = sorted(shot.pick.time for shot in chosen if shot.pick)
    assert all(b - a >= 20.0 for a, b in zip(times, times[1:], strict=False))


def test_select_shots_covers_timeline() -> None:
    # 开头信息密度最高，但结尾也必须至少有一张，否则教程会缺掉后半段
    shots = [_shot(0.0, 0.5), _shot(50.0, 0.08), _shot(95.0, 0.06)]
    chosen = select_shots(
        shots,
        duration=100.0,
        max_frames=3,
        min_gap_sec=5.0,
        coverage_buckets=4,
        min_score=0.01,
    )

    times = sorted(shot.pick.time for shot in chosen if shot.pick)
    assert len(times) == 3
    assert times[-1] >= 90.0


def test_select_shots_falls_back_when_all_face() -> None:
    shots = [_shot(0.0, 0.01), _shot(30.0, 0.02)]
    chosen = select_shots(
        shots,
        duration=60.0,
        max_frames=5,
        min_gap_sec=5.0,
        coverage_buckets=2,
        min_score=0.5,
    )
    assert len(chosen) == 1


def test_select_shots_empty() -> None:
    assert select_shots([], duration=10.0, max_frames=5, min_gap_sec=1.0) == []


# ------------------------------------------------------------------ 指标


def test_classify() -> None:
    assert classify(0.05, 0.5, 0.5) == "face"
    assert classify(0.30, 0.0, 0.9) == "slide"
    assert classify(0.05, 0.05, 0.85) == "whiteboard"
    assert classify(0.10, 0.05, 0.4) == "screen"


def test_info_score_penalizes_faces() -> None:
    assert info_score(0.3, 0.0) == 0.3
    assert info_score(0.3, 0.5) == 0.0
    assert info_score(0.0, 0.0) == 0.0


def test_kind_of_uses_sample_stat() -> None:
    sample = Sample(index=0, time=0.0, stat=FrameStat(edge=0.4, skin=0.0, brightness=0.8))
    assert kind_of(sample) == "slide"


def test_sharpness_detects_blur(tmp_path: Path) -> None:
    sharp = np.zeros((120, 120), dtype=np.uint8)
    sharp[::4, :] = 255
    sharp[:, ::4] = 255
    Image.fromarray(sharp).save(tmp_path / "sharp.png")

    flat = np.full((120, 120), 128, dtype=np.uint8)
    Image.fromarray(flat).save(tmp_path / "flat.png")

    from video2doc.visual.stats import load_gray

    assert sharpness(load_gray(tmp_path / "sharp.png")) > sharpness(
        load_gray(tmp_path / "flat.png")
    )


def test_edge_score_separates_text_from_flat() -> None:
    text = np.zeros((90, 160), dtype=np.uint8)
    text[10:20, 10:150] = 255
    text[30:40, 10:150] = 255
    flat = np.full((90, 160), 200, dtype=np.uint8)

    assert edge_score(text.astype(np.float32)) > edge_score(flat.astype(np.float32))


def test_dhash_same_content(tmp_path: Path) -> None:
    first = Image.new("RGB", (320, 180), "white")
    second = Image.new("RGB", (320, 180), "white")
    for image in (first, second):
        ImageDraw.Draw(image).rectangle([40, 40, 120, 100], fill="black")

    first.save(tmp_path / "a.jpg")
    second.save(tmp_path / "b.jpg")
    other = Image.new("RGB", (320, 180), "white")
    ImageDraw.Draw(other).rectangle([200, 20, 300, 160], fill="black")
    other.save(tmp_path / "c.jpg")

    a = dhash(tmp_path / "a.jpg")
    assert distance(a, dhash(tmp_path / "b.jpg")) == 0
    assert distance(a, dhash(tmp_path / "c.jpg")) > 2

"""镜头切分与选帧。纯函数，方便单测，也方便以后换成更好的策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .stats import FrameStat, classify, info_score


@dataclass
class Sample:
    index: int
    time: float
    stat: FrameStat
    hash: int = 0


@dataclass
class Shot:
    start: float
    end: float
    samples: list[Sample] = field(default_factory=list)
    score: float = 0.0
    pick: Sample | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def build_shots(
    samples: list[Sample],
    *,
    duration: float,
    interval: float,
    change_threshold: float = 0.10,
    hash_threshold: int = 12,
    min_shot_sec: float = 1.5,
) -> list[Shot]:
    """换镜头有两个信号，满足一个就切。

    - 像素差分：翻页、切人、镜头运动
    - 感知哈希：白底 PPT 换成另一页白底 PPT，整体亮度几乎没变，
      但版面完全不同，只靠像素差分经常漏掉
    """
    if not samples:
        return []

    boundaries = [0]
    for index in range(1, len(samples)):
        previous = samples[index - 1]
        current = samples[index]
        changed = current.stat.diff >= change_threshold or _hash_changed(
            previous.hash, current.hash, hash_threshold
        )
        if changed:
            boundaries.append(index)
    boundaries.append(len(samples))

    shots: list[Shot] = []
    for start_index, end_index in zip(boundaries, boundaries[1:], strict=False):
        group = samples[start_index:end_index]
        if not group:
            continue
        start = group[0].time
        end = min(duration, group[-1].time + interval)
        shots.append(Shot(start=start, end=end, samples=group))

    return _merge_short(shots, min_shot_sec)


def _merge_short(shots: list[Shot], min_shot_sec: float) -> list[Shot]:
    merged: list[Shot] = []
    for shot in shots:
        if merged and shot.duration < min_shot_sec:
            previous = merged[-1]
            previous.end = shot.end
            previous.samples.extend(shot.samples)
        else:
            merged.append(shot)

    # 开头就是一个极短镜头（例如片头闪一下）时，并进下一个而不是留成孤点
    if len(merged) > 1 and merged[0].duration < min_shot_sec:
        head = merged.pop(0)
        merged[0].start = head.start
        merged[0].samples = head.samples + merged[0].samples

    return merged


def score_shot(shot: Shot) -> float:
    if not shot.samples:
        return 0.0
    edge = sum(sample.stat.edge for sample in shot.samples) / len(shot.samples)
    skin = sum(sample.stat.skin for sample in shot.samples) / len(shot.samples)
    return info_score(edge, skin)


def pick_sample(shot: Shot) -> Sample:
    """在镜头的后 2/3 里挑边缘最强的一帧。

    白板逐笔书写时，最后写满的那一帧内容最全；
    翻页动画则会被 diff 切开，所以这里只处理「稳定画面内部」的挑选。
    """
    tail_start = len(shot.samples) // 3
    tail = shot.samples[tail_start:] or shot.samples
    return max(tail, key=lambda sample: (sample.stat.edge, sample.time))


def select_shots(
    shots: list[Shot],
    *,
    duration: float,
    max_frames: int,
    min_gap_sec: float,
    coverage_buckets: int = 8,
    min_score: float = 0.0,
) -> list[Shot]:
    """先保证时间轴覆盖，再按信息密度补齐。

    只用 top-N 会让「开头五分钟讲得最密」压掉后面的全部内容，
    而图文教程恰恰需要覆盖完整流程。
    """
    if not shots or max_frames <= 0:
        return []

    for shot in shots:
        shot.score = score_shot(shot)
        shot.pick = pick_sample(shot)

    viable = [shot for shot in shots if shot.score > min_score]
    if not viable:
        viable = sorted(shots, key=lambda shot: shot.score, reverse=True)[:1]

    chosen: list[Shot] = []
    picked: list[float] = []

    def try_add(shot: Shot) -> bool:
        if shot.pick is None or len(chosen) >= max_frames:
            return False
        time = shot.pick.time
        if any(abs(time - other) < min_gap_sec for other in picked):
            return False
        chosen.append(shot)
        picked.append(time)
        return True

    # 第一轮：每个时间桶最多选一个，避免整条视频只取材于某一段
    buckets: dict[int, Shot] = {}
    for shot in viable:
        index = _bucket(shot.pick.time, duration, coverage_buckets) if shot.pick else 0
        current = buckets.get(index)
        if current is None or shot.score > current.score:
            buckets[index] = shot
    for index in sorted(buckets):
        try_add(buckets[index])

    # 第二轮：按信息密度补齐剩余预算
    for shot in sorted(viable, key=lambda item: item.score, reverse=True):
        if len(chosen) >= max_frames:
            break
        try_add(shot)

    return sorted(chosen, key=lambda shot: shot.pick.time if shot.pick else 0.0)


def _bucket(time: float, duration: float, buckets: int) -> int:
    if duration <= 0 or buckets <= 0:
        return 0
    return min(buckets - 1, max(0, int(time / duration * buckets)))


def _hash_changed(previous: int, current: int, threshold: int) -> bool:
    if not previous or not current:
        return False
    return bin(previous ^ current).count("1") >= threshold


def kind_of(sample: Sample) -> str:
    return classify(sample.stat.edge, sample.stat.skin, sample.stat.brightness)

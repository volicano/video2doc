"""任务目录与 job.json 的读写。"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config, config_snapshot
from .errors import StageError
from .schema import Job
from .utils.common import now_iso
from .utils.url import douyin_video_id, extract_url

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"/av(\d+)", re.IGNORECASE)
_YT_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_DOUYIN_SHORT_RE = re.compile(r"v\.douyin\.com/([A-Za-z0-9_-]+)", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")


def derive_job_id(target: str) -> str:
    """尽量用视频平台的原生 ID（BV号 / YouTube / 抖音），可读又能复用。"""
    if match := _BV_RE.search(target):
        return match.group(1)
    if match := _AV_RE.search(target):
        return f"av{match.group(1)}"
    if match := _YT_RE.search(target):
        return match.group(1)
    if url := extract_url(target):
        if video_id := douyin_video_id(url):
            return video_id
    if match := _DOUYIN_SHORT_RE.search(target):
        return f"douyin-{match.group(1)}"

    path = Path(target).expanduser()
    stem = path.stem if path.suffix else target
    slug = _UNSAFE_RE.sub("-", stem).strip("-._")
    if not slug:
        return "job-" + str(abs(hash(target)))[:8]
    if len(slug) > 60:
        slug = slug[:48] + "-" + str(abs(hash(target)))[:8]
    return slug


def job_dir_for(out_root: Path, job_id: str) -> Path:
    return out_root / job_id


def open_job(job_dir: Path, target: str, config: Config) -> tuple[Job, bool]:
    """返回 (job, 是否新建)。已存在则复用，实现断点续跑。"""
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "job.json"

    if path.exists():
        try:
            job = Job.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise StageError(
                f"job.json 无法解析：{path}",
                hint="如果文件损坏，删掉整个任务目录重跑即可。",
            ) from exc
        if job.target != target:
            raise StageError(
                f"该任务目录属于另一个视频：{job.target}",
                hint="换一个 -o 目录，或删掉旧目录后重跑。",
            )
        return job, False

    job = Job(
        job_id=job_dir.name,
        target=target,
        created_at=now_iso(),
        updated_at=now_iso(),
        config_snapshot=config_snapshot(config),
        config_hash=config.hash,
    )
    save_job(job, job_dir)
    return job, True


def save_job(job: Job, job_dir: Path) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    job.updated_at = now_iso()
    (job_dir / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")

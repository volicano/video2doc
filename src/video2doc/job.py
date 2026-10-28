"""任务目录与 job.json 的读写。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Config, config_snapshot
from .errors import StageError
from .schema import Job
from .utils.common import now_iso
from .utils.files import atomic_write_text
from .utils.models import load_model
from .utils.url import douyin_video_id, extract_url, is_douyin_url

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"/av(\d+)", re.IGNORECASE)
_YT_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
_DOUYIN_SHORT_RE = re.compile(r"v\.douyin\.com/([A-Za-z0-9_-]+)", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _local_path(target: str) -> Path | None:
    """目标看起来是本地路径时返回 Path；URL / 裸平台 ID 返回 None。"""
    text = target.strip()
    if not text or extract_url(text):
        return None
    path = Path(text).expanduser()
    if path.is_file():
        return path
    # 有扩展名或路径分隔符的目标按本地文件处理。裸的 BV 号 / 抖音短码
    # 仍然交给平台解析，保持旧用法。
    if path.suffix or "/" in text or "\\" in text or text.startswith((".", "~")):
        return path
    return None


def _normalized_path(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        resolved = path.expanduser().absolute()
    return resolved.as_posix()


def _stable_hash(value: str, length: int = 8) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _local_job_id(path: Path) -> str:
    """本地任务 ID = 可读文件名 + 绝对路径摘要，避免同名文件互相覆盖。"""
    stem = path.stem if path.suffix else path.name
    slug = _UNSAFE_RE.sub("-", stem).strip("-._")
    if not slug:
        slug = "video"
    if len(slug) > 48:
        slug = slug[:48].rstrip("-._") or "video"
    return f"local-{slug}-{_stable_hash(_normalized_path(path))}"


def _bilibili_p(text: str) -> str | None:
    try:
        query = urlsplit(text).query
    except ValueError:
        return None
    value = parse_qs(query).get("p", [""])[0].strip()
    if value.isdigit() and int(value) > 1:
        return str(int(value))
    return None


def _bilibili_id(text: str) -> str | None:
    if match := _BV_RE.search(text):
        video_id = match.group(1)
    elif match := _AV_RE.search(text):
        video_id = f"av{match.group(1)}"
    else:
        return None
    if part := _bilibili_p(text):
        return f"{video_id}-p{part}"
    return video_id


def _youtube_id(text: str) -> str | None:
    match = _YT_RE.search(text)
    return match.group(1) if match else None


def _douyin_short_code(text: str) -> str | None:
    match = _DOUYIN_SHORT_RE.search(text)
    return match.group(1) if match else None


def _is_bilibili_host(host: str) -> bool:
    host = host.lower()
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _is_youtube_host(host: str) -> bool:
    host = host.lower()
    return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"


def _normalize_url(url: str) -> str:
    """去掉 fragment、默认端口和查询参数顺序差异，便于比较同一个链接。"""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return url.strip()
    if not host:
        return url.strip()

    if ":" in host:
        host = f"[{host}]"
    if port and port != _DEFAULT_PORTS.get(parts.scheme.lower()):
        host = f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _url_identity(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return f"url:{url.strip()}"

    if _is_bilibili_host(host):
        if video_id := _bilibili_id(url):
            return f"bilibili:{video_id}"
    if _is_youtube_host(host):
        if video_id := _youtube_id(url):
            return f"youtube:{video_id}"
    if is_douyin_url(url):
        if video_id := douyin_video_id(url):
            return f"douyin:{video_id}"
        if code := _douyin_short_code(url):
            return f"douyin-short:{code}"
    return f"url:{_normalize_url(url)}"


def _url_job_id(url: str) -> str:
    identity = _url_identity(url)
    for prefix in ("bilibili:", "youtube:", "douyin:"):
        if identity.startswith(prefix):
            return identity.removeprefix(prefix)
    if identity.startswith("douyin-short:"):
        return f"douyin-{identity.removeprefix('douyin-short:')}"
    return f"url-{_stable_hash(identity, 12)}"


def target_identity(target: str) -> str:
    """把分享文本、短链、长链和本地路径归一到稳定的任务身份。

    同一个视频的分享文案/查询参数不同、同一个本地文件的相对/绝对路径不同，
    都应该复用同一个任务目录；B 站分 P 则必须视为不同视频。
    """
    text = target.strip()
    if local := _local_path(text):
        return f"file:{_normalized_path(local)}"
    if url := extract_url(text):
        return _url_identity(url)
    if video_id := _bilibili_id(text):
        return f"bilibili:{video_id}"
    if video_id := _youtube_id(text):
        return f"youtube:{video_id}"
    if code := _douyin_short_code(text):
        return f"douyin-short:{code}"
    return f"file:{_normalized_path(Path(text).expanduser())}"


def derive_job_id(target: str) -> str:
    """尽量用视频平台的原生 ID（BV号 / YouTube / 抖音），可读又能复用。"""
    text = target.strip()
    if local := _local_path(text):
        return _local_job_id(local)

    url = extract_url(text)
    if url:
        return _url_job_id(url)

    if video_id := _bilibili_id(text):
        return video_id
    if video_id := _youtube_id(text):
        return video_id
    if code := _douyin_short_code(text):
        return f"douyin-{code}"
    return _local_job_id(Path(text).expanduser())


def job_dir_for(out_root: Path, job_id: str) -> Path:
    return out_root / job_id


def read_job(job_dir: Path) -> Job:
    """读取 job.json，并把缺文件 / 损坏统一变成可读的 StageError。"""
    path = job_dir / "job.json"
    if not path.exists():
        raise StageError(
            f"找不到任务文件：{path}",
            hint="先运行 v2doc run，或检查任务目录是否正确。",
        )
    return load_model(
        path,
        Job,
        label="job.json",
        hint="如果文件损坏，删掉整个任务目录重跑即可。",
    )


def open_job(
    job_dir: Path,
    target: str,
    config: Config,
    *,
    identity: str | None = None,
) -> tuple[Job, bool]:
    """返回 (job, 是否新建)。已存在则复用，实现断点续跑。"""
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StageError(
            f"无法创建任务目录：{job_dir}",
            hint="检查输出目录是否可写。",
        ) from exc
    path = job_dir / "job.json"
    expected = identity or target_identity(target)

    if path.exists():
        job = read_job(job_dir)
        actual = job.target_identity or target_identity(job.target)
        if actual != expected:
            raise StageError(
                f"该任务目录属于另一个视频：{job.target}",
                hint="换一个 -o 目录，或删掉旧目录后重跑。",
            )
        if not job.target_identity:
            # 兼容旧 job.json：迁移时把归一化身份补上，之后换工作目录也能续跑。
            job.target_identity = actual
            save_job(job, job_dir)
        return job, False

    job = Job(
        job_id=job_dir.name,
        target=target,
        target_identity=expected,
        created_at=now_iso(),
        updated_at=now_iso(),
        config_snapshot=config_snapshot(config),
        config_hash=config.hash,
    )
    save_job(job, job_dir)
    return job, True


def save_job(job: Job, job_dir: Path) -> None:
    job.updated_at = now_iso()
    try:
        atomic_write_text(job_dir / "job.json", job.model_dump_json(indent=2))
    except OSError as exc:
        raise StageError(
            f"无法写入 job.json：{job_dir / 'job.json'}",
            hint="检查磁盘空间和目录权限。",
        ) from exc

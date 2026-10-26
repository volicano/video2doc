"""ingest 阶段：把「一条链接」或「一个本地文件」变成 SourceInfo。"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import yt_dlp
from pydantic import BaseModel
from yt_dlp.utils import DownloadError

from ..config import Config
from ..errors import StageError
from ..pipeline import Context, Stage
from ..schema import SourceInfo, SubtitleFile
from ..utils.common import hms
from ..utils.media import probe
from ..utils.proc import ffmpeg_exe
from ..utils.url import (
    canonicalize_douyin_url,
    douyin_video_id,
    extract_url,
    is_douyin_short_url,
    is_douyin_url,
)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".flv", ".mov", ".m4v", ".avi", ".ts", ".mpg", ".mpeg"}
SUBTITLE_EXTS = {".vtt", ".srt"}

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$")


class IngestStage(Stage):
    name = "ingest"
    title = "解析视频源"
    version = 1

    def params(self, config: Config) -> dict:
        return {
            "max_height": config.video.max_height,
            "prefer_subtitles": config.video.prefer_subtitles,
            "subtitle_langs": config.video.subtitle_langs,
            "cookies_from_browser": config.video.cookies_from_browser,
            "max_duration_min": config.video.max_duration_min,
            "proxy": config.network.proxy,
        }

    def run(self, ctx: Context) -> BaseModel:
        target = ctx.job.target
        path = Path(target).expanduser()
        url = extract_url(target)

        if path.exists() and path.is_file():
            info = _ingest_local(ctx, path)
        elif url:
            info = _ingest_url(ctx, url)
        else:
            raise StageError(
                f"找不到视频：{target}",
                hint="传入本地视频文件路径、视频链接，或直接粘贴抖音分享文本。",
            )

        _check_duration(info, ctx.config.video.max_duration_min)
        ctx.job.source = info
        return info

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, SourceInfo)
        parts = [result.title[:40]]
        if result.duration:
            parts.append(hms(result.duration))
        if result.subtitles:
            langs = ", ".join(sorted({s.language for s in result.subtitles}))
            parts.append(f"字幕 {langs}")
        else:
            parts.append("无字幕")
        return " · ".join(parts)


# ---------------------------------------------------------------- 本地文件


def _ingest_local(ctx: Context, path: Path) -> SourceInfo:
    ctx.progress("读取媒体信息…")
    media = probe(path)
    if not media.has_video:
        raise StageError(f"这不是视频文件：{path}")

    subtitles = _find_sidecar_subtitles(path)
    if not media.has_audio and not subtitles:
        raise StageError(
            f"视频既没有音轨也没有字幕：{path}",
            hint="换一个有声音的源，或把同名字幕文件（.vtt/.srt）放到视频旁边。",
        )

    return SourceInfo(
        kind="file",
        path=str(path.resolve()),
        title=path.stem,
        duration=media.duration,
        subtitles=subtitles,
        downloaded=False,
    )


def _find_sidecar_subtitles(video: Path) -> list[SubtitleFile]:
    """同名字幕：video.zh.vtt / video.zh-Hans.srt / video.srt。"""
    found: list[SubtitleFile] = []
    for item in sorted(video.parent.iterdir()):
        if not item.is_file() or item == video:
            continue
        if item.suffix.lower() not in SUBTITLE_EXTS:
            continue
        if not item.name.startswith(video.stem):
            continue
        found.append(
            SubtitleFile(
                path=str(item.resolve()),
                language=_language_from_name(item.name, video.stem, item.suffix),
                auto=_looks_auto(item.name),
                format=item.suffix.lstrip(".").lower(),
            )
        )
    return found


def _language_from_name(name: str, stem: str, suffix: str) -> str:
    middle = name[len(stem) : len(name) - len(suffix)].strip(".")
    for token in reversed(middle.split(".")):
        if _LANG_RE.match(token) and token.lower() not in {"auto", "orig"}:
            return token
    return "und"


def _looks_auto(name: str) -> bool:
    return ".auto." in name.lower() or name.lower().endswith(".auto.vtt")


# ------------------------------------------------------------------ 在线视频


_DOUYIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_DOUYIN_CANONICAL_RE = re.compile(r"https?://(?:www\.)?douyin\.com/video/\d+")


def _resolve_douyin_url(ctx: Context, url: str) -> str:
    """抖音分享页直接归一化；短链先跟随重定向，拿到 /video/<id>。"""
    url = canonicalize_douyin_url(url)
    if not is_douyin_short_url(url):
        return url

    ctx.progress("解析抖音短链…")
    kwargs: dict = {
        "follow_redirects": True,
        "timeout": 15.0,
        "headers": {"User-Agent": _DOUYIN_UA},
    }
    if ctx.config.network.proxy:
        kwargs["proxy"] = ctx.config.network.proxy

    try:
        with httpx.Client(**kwargs) as client:
            response = client.get(url)
        final_url = str(response.url)
        if douyin_video_id(final_url):
            return canonicalize_douyin_url(final_url)
        match = _DOUYIN_CANONICAL_RE.search(response.text)
        if match:
            return canonicalize_douyin_url(match.group(0))
        ctx.detail("短链没有跳到视频页，继续交给 yt-dlp 处理。")
    except (httpx.HTTPError, ValueError) as exc:
        ctx.detail(f"短链解析失败，继续交给 yt-dlp：{exc}")
    return url


def _download_hint(url: str, message: str) -> str:
    if is_douyin_url(url) or "douyin" in message.lower():
        return (
            "抖音通常需要浏览器 Cookie：先在配置里设置 "
            'video.cookies_from_browser = "chrome"（或 edge / firefox），'
            "并完全退出对应浏览器后重试。也可以先运行 "
            "`yt-dlp --cookies-from-browser chrome <抖音链接>` 验证。"
            "如果提示短链失效，请重新复制分享链接；地区限制则配置 network.proxy。"
        )
    return (
        "常见原因：需要登录（配置 cookies_from_browser）、地区限制（配置 network.proxy）、"
        "或链接已失效。先用 `yt-dlp -F <url>` 手动确认能否下载。"
    )


def _ingest_url(ctx: Context, url: str) -> SourceInfo:
    config = ctx.config
    url = _resolve_douyin_url(ctx, url)
    if is_douyin_url(url) and not config.video.cookies_from_browser:
        ctx.detail(
            "抖音通常需要浏览器 Cookie；若下载失败，请在配置里设置 "
            'video.cookies_from_browser = "chrome"。'
        )
    media_dir = ctx.job_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    langs: list[str] = []
    for lang in [config.asr.language, *config.video.subtitle_langs]:
        if lang and lang not in langs:
            langs.append(lang)

    height = config.video.max_height
    options: dict = {
        "paths": {"home": str(media_dir)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "format": f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "writeinfojson": True,
        "writesubtitles": config.video.prefer_subtitles,
        "writeautomaticsub": config.video.prefer_subtitles,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [_progress_hook(ctx)],
        "ffmpeg_location": str(Path(ffmpeg_exe()).parent),
    }
    if config.network.proxy:
        options["proxy"] = config.network.proxy
    if config.video.cookies_from_browser:
        options["cookiesfrombrowser"] = (config.video.cookies_from_browser,)

    ctx.progress("解析链接…")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise StageError(f"下载失败：{exc}", hint=_download_hint(url, str(exc))) from exc

    if not info:
        raise StageError("没有拿到视频信息", hint="检查链接是否有效。")
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        if not entries:
            raise StageError("播放列表为空")
        ctx.detail("检测到多 P 视频，只处理第一 P。")
        info = entries[0]

    video_id = str(info.get("id") or "")
    video_path = _find_downloaded_video(media_dir, video_id)
    if video_path is None:
        raise StageError(
            "下载完成但找不到视频文件",
            hint=f"检查目录：{media_dir}",
        )

    ctx.progress("读取媒体信息…")
    media = probe(video_path)
    if not media.has_audio:
        raise StageError("该视频没有音轨", hint="如果它自带字幕，可以等 M2 阶段的纯画面模式。")

    subtitles = _collect_subtitles(media_dir, video_id, info)

    return SourceInfo(
        kind="url",
        path=str(video_path.resolve()),
        title=str(info.get("title") or video_id or "未命名视频"),
        duration=info.get("duration") or media.duration,
        uploader=info.get("uploader") or info.get("channel"),
        description=(info.get("description") or "")[:2000] or None,
        tags=[str(tag) for tag in (info.get("tags") or [])],
        url=url,
        webpage_url=info.get("webpage_url") or url,
        video_id=video_id or None,
        subtitles=subtitles,
        downloaded=True,
        thumbnail=info.get("thumbnail"),
    )


def _progress_hook(ctx: Context):
    def hook(data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            percent = str(data.get("_percent_str") or "").strip()
            speed = str(data.get("_speed_str") or "").strip()
            ctx.progress(f"下载中 {percent} {speed}".strip())
        elif status == "finished":
            ctx.progress("下载完成，正在合并音视频…")

    return hook


def _find_downloaded_video(media_dir: Path, video_id: str) -> Path | None:
    if not video_id:
        candidates = [p for p in media_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS]
        return max(candidates, key=lambda p: p.stat().st_size) if candidates else None
    for item in media_dir.iterdir():
        if item.suffix.lower() in VIDEO_EXTS and item.name.startswith(f"{video_id}."):
            return item
    return None


def _collect_subtitles(media_dir: Path, video_id: str, info: dict) -> list[SubtitleFile]:
    manual = set((info.get("subtitles") or {}).keys())
    automatic = set((info.get("automatic_captions") or {}).keys())

    found: list[SubtitleFile] = []
    for item in sorted(media_dir.iterdir()):
        if item.suffix.lower() not in SUBTITLE_EXTS:
            continue
        if video_id and not item.name.startswith(f"{video_id}."):
            continue
        language = _language_from_name(item.name, video_id, item.suffix) if video_id else "und"
        is_auto = language not in manual and language in automatic
        found.append(
            SubtitleFile(
                path=str(item.resolve()),
                language=language,
                auto=is_auto,
                format=item.suffix.lstrip(".").lower(),
            )
        )
    return found


def _check_duration(info: SourceInfo, max_minutes: int) -> None:
    if not info.duration or max_minutes <= 0:
        return
    if info.duration > max_minutes * 60:
        raise StageError(
            f"视频时长 {hms(info.duration)} 超过上限 {max_minutes} 分钟",
            hint="在配置文件里调大 video.max_duration_min，或换成更短的素材。",
        )

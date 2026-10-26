"""链接归一化：把分享文本里的链接、抖音短链和长链统一成可下载的地址。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_URL_RE = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}，。！？、；：）】》」』"
_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
_DOUYIN_VIDEO_RE = re.compile(r"/(?:video|note)/(\d+)")
_DOUYIN_SHARE_RE = re.compile(r"/share/(?:video|note)/(\d+)")


def extract_url(text: str) -> str | None:
    """从一段分享文本里取出第一个 http(s) 链接。"""
    match = _URL_RE.search(text.strip())
    if not match:
        return None
    url = match.group(0).rstrip(_TRAILING)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _is_douyin_host(host: str) -> bool:
    host = host.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in _DOUYIN_DOMAINS)


def is_douyin_url(url: str) -> bool:
    return _is_douyin_host(urlparse(url).netloc)


def is_douyin_short_url(url: str) -> bool:
    return urlparse(url).netloc.lower() == "v.douyin.com"


def douyin_video_id(url: str) -> str | None:
    """取抖音长链或分享页里的视频 ID；短链需要先解析重定向。"""
    parsed = urlparse(url)
    if not _is_douyin_host(parsed.netloc):
        return None
    match = _DOUYIN_VIDEO_RE.search(parsed.path) or _DOUYIN_SHARE_RE.search(parsed.path)
    if match:
        return match.group(1)

    # 发现页/用户页常用 ?modal_id=<id> 指向单条视频。
    query = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_id"):
        value = query.get(key, [""])[0]
        if value.isdigit():
            return value
    return None


def canonicalize_douyin_url(url: str) -> str:
    """抖音分享页/长链统一成 yt-dlp DouyinIE 认识的长链。"""
    video_id = douyin_video_id(url)
    if video_id:
        return f"https://www.douyin.com/video/{video_id}"
    return url

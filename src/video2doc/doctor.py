"""v2doc doctor：一次性告诉你缺什么、怎么补。

这个命令的目标是把一半的 issue 挡在门外。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .config import Config
from .providers.asr import available_providers
from .utils.proc import ffmpeg_exe, ffprobe_exe

Status = Literal["ok", "warn", "fail"]

_ICONS: dict[Status, str] = {"ok": "[green]OK[/]", "warn": "[yellow]!![/]", "fail": "[red]XX[/]"}


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


def run_doctor(config: Config, console: Console, *, check_network: bool = True) -> int:
    checks: list[Check] = [
        _check_python(),
        _check_ffmpeg(),
        _check_ffprobe(),
        _check_ytdlp(),
        *_check_asr(config),
        _check_llm(config),
        _check_ocr(),
        _check_output_dir(),
    ]
    if check_network:
        checks.extend(_check_network(config))

    table = Table(title=f"video2doc {__version__} 环境检查", show_lines=False, pad_edge=False)
    table.add_column("", width=4, justify="center")
    table.add_column("项目", style="bold", width=18)
    table.add_column("详情", overflow="fold")
    table.add_column("修复建议", style="dim", overflow="fold")

    for check in checks:
        table.add_row(
            _ICONS[check.status],
            escape(check.name),
            escape(check.detail),
            escape(check.fix),
        )

    console.print(table)

    failures = [check for check in checks if check.status == "fail"]
    warnings = [check for check in checks if check.status == "warn"]
    if failures:
        console.print(f"[red]有 {len(failures)} 项必须解决，否则跑不起来。[/]")
        return 1
    if warnings:
        console.print(f"[yellow]{len(warnings)} 项需要注意，但不影响基本运行。[/]")
    else:
        console.print("[green]一切就绪，可以开始：v2doc run <视频链接>[/]")
    return 0


def _check_python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 11):  # noqa: UP036 - 用户可能用别的解释器直接跑源码
        return Check("Python", "fail", version, "需要 3.11 及以上")
    return Check("Python", "ok", version)


def _check_ffmpeg() -> Check:
    try:
        path = ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        return Check("ffmpeg", "fail", str(exc), "winget install Gyan.FFmpeg")
    return Check("ffmpeg", "ok", path)


def _check_ffprobe() -> Check:
    try:
        path = ffprobe_exe()
    except Exception as exc:  # noqa: BLE001
        return Check("ffprobe", "fail", str(exc), "通常随 ffmpeg 一起安装")
    return Check("ffprobe", "ok", path)


def _check_ytdlp() -> Check:
    try:
        from yt_dlp.version import __version__ as version
    except Exception:  # noqa: BLE001
        return Check("yt-dlp", "fail", "未安装", "pip install -U yt-dlp")
    return Check("yt-dlp", "ok", version, "下载失败时先试 pip install -U yt-dlp")


def _check_asr(config: Config) -> list[Check]:
    checks: list[Check] = []
    for status in available_providers():
        if status.name == config.asr.provider:
            if status.ready:
                checks.append(Check(f"ASR · {status.name}", "ok", f"{status.detail}（当前使用）"))
            else:
                checks.append(
                    Check(
                        f"ASR · {status.name}",
                        "fail",
                        f"{status.detail}（当前使用）",
                        'pip install "video2doc[local]" 或改 asr.provider = "openai"',
                    )
                )
        else:
            checks.append(
                Check(f"ASR · {status.name}", "ok" if status.ready else "warn", status.detail)
            )
    if config.asr.provider == "openai" and not os.environ.get(config.asr.api_key_env):
        checks.append(
            Check(
                "ASR 密钥",
                "fail",
                f"未设置 {config.asr.api_key_env}",
                f'$env:{config.asr.api_key_env}="..."',
            )
        )
    return checks


def _check_llm(config: Config) -> Check:
    detail = f"{config.llm.model} @ {config.llm.base_url}"
    if not config.llm.api_key_env:
        return Check("LLM", "ok", f"{detail}（无密钥模式）")
    if os.environ.get(config.llm.api_key_env):
        return Check("LLM", "ok", detail)
    return Check(
        "LLM 密钥",
        "warn",
        f"未设置 {config.llm.api_key_env}",
        "写作（write）会用到；本地模型可把 api_key_env 设为空字符串",
    )


def _check_ocr() -> Check:
    modules = [module for module in ("rapidocr_onnxruntime", "numpy", "PIL") if _has_module(module)]
    if len(modules) == 3:
        return Check("OCR", "ok", "rapidocr-onnxruntime 已安装（离线）")
    return Check(
        "OCR",
        "warn",
        f"缺少：{3 - len(modules)} 个模块，抽帧仍可运行，但读不到画面文字",
        'pip install "video2doc[ocr]"',
    )


def _check_output_dir() -> Check:
    target = Path("output")
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".video2doc-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("输出目录", "fail", str(exc), "换一个有写权限的 -o 目录")
    return Check("输出目录", "ok", str(target.resolve()))


def _check_network(config: Config) -> list[Check]:
    endpoints = [
        ("B站", "https://www.bilibili.com"),
        ("YouTube", "https://www.youtube.com"),
        ("抖音", "https://www.douyin.com"),
    ]
    checks: list[Check] = []
    try:
        import httpx
    except ImportError:
        return [Check("网络", "warn", "未安装 httpx，跳过检测")]

    kwargs: dict = {"timeout": 6, "follow_redirects": True}
    if config.network.proxy:
        kwargs["proxy"] = config.network.proxy

    for name, url in endpoints:
        try:
            response = httpx.head(url, **kwargs)
            checks.append(
                Check(f"网络 · {name}", "ok", f"HTTP {response.status_code}")
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check(
                    f"网络 · {name}",
                    "warn",
                    type(exc).__name__,
                    "配置 network.proxy 或检查网络",
                )
            )
    return checks


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False

"""面向用户的错误类型：带 hint，方便 CLI 直接打印修复建议。"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


class Video2DocError(Exception):
    """所有可预期错误的基类。"""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self, console: Console) -> None:
        body = self.message
        if self.hint:
            body += f"\n\n[dim]建议：[/]{self.hint}"
        console.print(Panel(body, title="[red]出错了[/]", border_style="red", padding=(0, 1)))


class ConfigError(Video2DocError):
    """配置文件不合法。"""


class StageError(Video2DocError):
    """流水线某一步执行失败。"""


class ToolMissingError(Video2DocError):
    """外部依赖（ffmpeg / yt-dlp / 模型）缺失。"""

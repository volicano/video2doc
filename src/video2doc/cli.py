"""命令行入口。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import EXAMPLE_CONFIG, Config, load_config
from .doctor import run_doctor
from .errors import StageError, Video2DocError
from .job import derive_job_id, job_dir_for, open_job
from .pipeline import Pipeline
from .providers.asr import available_providers
from .providers.llm import available_providers as llm_available_providers
from .providers.ocr import available_providers as ocr_available_providers
from .schema import FrameSelection, Job, OCRResult, Transcript
from .stages import default_stages
from .utils.common import hms

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="把面试 / 教学视频转成图文教程。\n\n示例：v2doc run \"https://www.bilibili.com/video/BVxxxx\"",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"video2doc {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True, help="显示版本"
    ),
) -> None:
    """video2doc —— 视频转图文教程。"""


# --------------------------------------------------------------------- run


@app.command()
def run(
    target: str = typer.Argument(..., help="视频链接（B站 / YouTube）或本地文件路径"),
    out: Path = typer.Option(Path("output"), "--out", "-o", help="输出根目录"),
    job_dir: Path | None = typer.Option(None, "--job-dir", help="直接指定本次任务目录"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
    from_stage: str | None = typer.Option(None, "--from", help="从某个阶段开始强制重跑"),
    force: bool = typer.Option(False, "--force", help="忽略缓存，全部重跑"),
) -> None:
    """处理一条视频。重复执行同一条命令会自动续跑。"""
    try:
        cfg = load_config(config)
        resolved = job_dir or job_dir_for(out, derive_job_id(target))
        job, created = open_job(resolved, target, cfg)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    _print_header(cfg, job, resolved, created)
    _run_pipeline(job, resolved, cfg, force=force, from_stage=from_stage)


@app.command()
def resume(
    job_dir: Path = typer.Argument(..., help="任务目录（包含 job.json）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
    from_stage: str | None = typer.Option(None, "--from", help="从某个阶段开始强制重跑"),
    force: bool = typer.Option(False, "--force", help="忽略缓存，全部重跑"),
) -> None:
    """继续一个未完成或需要重跑的任务。"""
    job_path = job_dir / "job.json"
    if not job_path.exists():
        console.print(f"[red]找不到任务文件：{job_path}[/]")
        raise typer.Exit(1)

    try:
        old = Job.model_validate_json(job_path.read_text(encoding="utf-8"))
        cfg = load_config(config)
        job, created = open_job(job_dir, old.target, cfg)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    _print_header(cfg, job, job_dir, created)
    _run_pipeline(job, job_dir, cfg, force=force, from_stage=from_stage)


# ------------------------------------------------------------------ doctor


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
    network: bool = typer.Option(True, "--network/--no-network", help="是否检测网络连通性"),
) -> None:
    """检查 ffmpeg / yt-dlp / ASR / 密钥等依赖。"""
    try:
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None
    raise typer.Exit(run_doctor(cfg, console, check_network=network))


@app.command("providers")
def providers_command(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """列出可用的语音识别服务。"""
    try:
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    table = Table(title="ASR providers")
    table.add_column("名称", style="bold")
    table.add_column("说明")
    table.add_column("状态")
    for status in available_providers():
        mark = "[green]可用[/]" if status.ready else f"[yellow]{status.detail}[/]"
        if status.name == cfg.asr.provider:
            mark += " [cyan](当前)[/]"
        table.add_row(status.name, status.label, mark)
    console.print(table)

    llm_table = Table(title="LLM providers（校正与写作）")
    llm_table.add_column("名称", style="bold")
    llm_table.add_column("说明")
    llm_table.add_column("状态")
    for status in llm_available_providers():
        mark = "[green]可用[/]" if status.ready else f"[yellow]{status.detail}[/]"
        if status.name == cfg.llm.provider:
            mark += " [cyan](当前)[/]"
        llm_table.add_row(status.name, status.label, mark)
    console.print(llm_table)

    ocr_table = Table(title="OCR providers（画面文字）")
    ocr_table.add_column("名称", style="bold")
    ocr_table.add_column("说明")
    ocr_table.add_column("状态")
    for status in ocr_available_providers():
        mark = "[green]可用[/]" if status.ready else f"[yellow]{status.detail}[/]"
        if status.name == cfg.ocr.provider:
            mark += " [cyan](当前)[/]"
        ocr_table.add_row(status.name, status.label, mark)
    console.print(ocr_table)


# ----------------------------------------------------------------- preview


@app.command()
def preview(
    job_dir: Path = typer.Argument(..., help="任务目录"),
    limit: int = typer.Option(0, "--limit", "-n", help="只显示前 N 句（0 表示全部）"),
    export: Path | None = typer.Option(None, "--export", help="导出为带时间戳的纯文本"),
) -> None:
    """查看（或导出）已生成的转写文本。"""
    transcript = _load_transcript(job_dir)
    utterances = transcript.utterances
    if limit > 0:
        utterances = utterances[:limit]

    console.print(
        f"[dim]{transcript.provider} · {transcript.summary()}[/]"
        + (
            f" [green]已校正 {transcript.corrections} 处[/]"
            if transcript.corrections
            else ""
        )
        + "\n"
    )
    for utterance in utterances:
        console.print(f"[cyan]{hms(utterance.start):>8}[/]  {escape(utterance.text)}")

    if export:
        lines = [f"[{hms(u.start)}] {u.text}" for u in transcript.utterances]
        export.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"\n[green]已导出[/] {export}")


@app.command()
def frames(
    job_dir: Path = typer.Argument(..., help="任务目录"),
    limit: int = typer.Option(0, "--limit", "-n", help="只显示前 N 张（0 表示全部）"),
    open_preview: bool = typer.Option(False, "--open", help="用浏览器打开 preview.html"),
) -> None:
    """查看抽帧结果（时间、类型、画面文字）。"""
    selection = _load_stage(job_dir, "frames", FrameSelection)
    ocr_chars: dict[int, int] = {}
    try:
        ocr = _load_stage(job_dir, "ocr", OCRResult)
        ocr_chars = {frame.index: frame.char_count for frame in ocr.frames}
    except Video2DocError:
        pass

    shown = selection.frames[: limit or None]
    table = Table(title=f"选中画面 · {selection.summary()}")
    table.add_column("时间", style="cyan", justify="right")
    table.add_column("类型")
    table.add_column("文字", justify="right")
    table.add_column("得分", justify="right")
    table.add_column("原图", overflow="fold")
    for frame in shown:
        table.add_row(
            hms(frame.time),
            frame.kind,
            str(ocr_chars.get(frame.index, 0)),
            f"{frame.score:.3f}",
            frame.path,
        )
    console.print(table)

    preview = job_dir / "preview.html"
    if preview.exists():
        console.print(f"[dim]调试图：[/]{preview}")
        if open_preview:
            import webbrowser

            webbrowser.open(preview.resolve().as_uri())


@app.command("open")
def open_doc(
    job_dir: Path = typer.Argument(..., help="任务目录"),
    markdown: bool = typer.Option(False, "--md", help="打开导出的 tutorial.md"),
) -> None:
    """用浏览器打开生成的图文教程。"""
    target = job_dir / ("tutorial.md" if markdown else "tutorial.html")
    if not target.exists():
        preview = job_dir / "preview.html"
        if not markdown and preview.exists():
            console.print("[yellow]还没有教程，先打开抽帧调试图。[/]")
            target = preview
        else:
            console.print(f"[red]找不到 {target}[/]\n[dim]先运行 v2doc run 生成教程。[/]")
            raise typer.Exit(1)

    import webbrowser

    webbrowser.open(target.resolve().as_uri())
    console.print(f"[green]已打开[/] {target}")


# -------------------------------------------------------------------- init


@app.command()
def init(
    path: Path = typer.Option(Path("video2doc.toml"), "--path", help="写入位置"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的文件"),
) -> None:
    """生成一份带注释的配置文件。"""
    if path.exists() and not force:
        console.print(f"[yellow]{path} 已存在，加 --force 覆盖。[/]")
        raise typer.Exit(1)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    console.print(f"[green]已写入[/] {path}")


# ----------------------------------------------------------------- helpers


def _run_pipeline(
    job: Job,
    job_dir: Path,
    cfg: Config,
    *,
    force: bool,
    from_stage: str | None,
) -> None:
    pipeline = Pipeline(
        default_stages(),
        job,
        job_dir,
        cfg,
        console,
        force=force,
        start_at=from_stage,
    )
    try:
        job = pipeline.run()
    except Video2DocError as exc:
        console.print()
        exc.render(console)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断。任务已保存，重跑同一条命令即可继续。[/]")
        raise typer.Exit(130) from None

    _print_summary(job, job_dir)


def _print_header(cfg: Config, job: Job, job_dir: Path, created: bool) -> None:
    action = "新任务" if created else "继续任务"
    console.print(
        Panel(
            f"[bold]{escape(job.target)}[/]\n"
            f"[dim]{action} · {escape(str(job_dir))} · "
            f"ASR={cfg.asr.provider}/{cfg.asr.language}[/]",
            title=f"[cyan]video2doc {__version__}[/]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


def _print_summary(job: Job, job_dir: Path) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", width=12)
    table.add_column()

    if job.source:
        table.add_row("标题", escape(job.source.title))
        if job.source.duration:
            table.add_row("时长", hms(job.source.duration))
        if job.source.uploader:
            table.add_row("作者", escape(job.source.uploader))

    for name, record in job.stages.items():
        if record.status == "done":
            detail = f"{record.summary}  [{record.elapsed:.1f}s]"
        elif record.status == "skipped":
            detail = f"[yellow]{record.summary}[/]"
        else:
            detail = f"[red]{record.error or '失败'}[/]"
        table.add_row(name, detail)  # type: ignore[arg-type]

    table.add_row("目录", escape(str(job_dir.resolve())))

    console.print()
    console.print(Panel(table, title="[green]流水线[/]", border_style="green", padding=(0, 1)))

    tutorial = job_dir / "tutorial.html"
    console.print("[dim]下一步：[/]")
    if tutorial.exists():
        console.print(f"  v2doc open {job_dir}          [dim]# 打开图文教程[/]")
        console.print(f"  [dim]教程：{tutorial}[/]")
    console.print(f"  v2doc preview {job_dir}")
    preview = job_dir / "preview.html"
    if preview.exists():
        console.print(f"  v2doc frames {job_dir} --open   [dim]# 看一眼抽帧和 OCR[/]")
        console.print(f"  [dim]调试图：{preview}[/]")
    if not tutorial.exists():
        console.print(
            "[dim]提示：[/]需要 LLM 才能生成教程；不想调用模型可设 write.enabled = false。"
        )


def _load_transcript(job_dir: Path) -> Transcript:
    job = _read_job(job_dir)
    record = None
    for name in ("correct", "transcript"):
        candidate = job.stages.get(name)
        if candidate is not None and candidate.status == "done" and candidate.output:
            record = candidate
            break
    if record is None:
        console.print("[red]还没有转写结果，先运行 v2doc run。[/]")
        raise typer.Exit(1)

    path = job_dir / record.output
    if not path.exists():
        console.print(f"[red]转写文件丢失：{path}[/]")
        raise typer.Exit(1)
    return Transcript.model_validate_json(path.read_text(encoding="utf-8"))


_T = TypeVar("_T", bound=BaseModel)


def _read_job(job_dir: Path) -> Job:
    job_path = job_dir / "job.json"
    if not job_path.exists():
        console.print(f"[red]找不到任务文件：{job_path}[/]")
        raise typer.Exit(1)
    return Job.model_validate_json(job_path.read_text(encoding="utf-8"))


def _load_stage(job_dir: Path, name: str, model: type[_T]) -> _T:
    job = _read_job(job_dir)
    record = job.stages.get(name)
    if record is None or record.status != "done" or not record.output:
        raise StageError(f"还没有 {name} 阶段的产物", hint="先运行 v2doc run。")
    path = job_dir / record.output
    if not path.exists():
        raise StageError(f"产物文件丢失：{path}", hint="加上 --force 重跑。")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main() -> None:
    _ensure_utf8()
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断。[/]")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()

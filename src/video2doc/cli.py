"""命令行入口。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
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
from .evaluate import EvalReport, evaluate_job
from .job import derive_job_id, job_dir_for, open_job
from .pipeline import Pipeline
from .providers.asr import available_providers
from .providers.llm import available_providers as llm_available_providers
from .providers.ocr import available_providers as ocr_available_providers
from .review import serve as serve_review
from .revision import regenerate_section
from .schema import FrameSelection, Job, OCRResult, Transcript, UsageStats
from .stages import default_stages
from .utils.common import hms
from .writing import llm_unavailable

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help='把面试 / 教学视频转成图文教程。\n\n示例：v2doc run "https://www.bilibili.com/video/BVxxxx"',
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
    target: str = typer.Argument(
        ..., help="视频链接（B站 / YouTube / 抖音，支持分享文本）或本地文件路径"
    ),
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


# -------------------------------------------------------- rewrite / render


@app.command()
def rewrite(
    job_dir: Path = typer.Argument(..., help="任务目录（包含 job.json）"),
    section: int = typer.Option(..., "--section", "-s", min=1, help="要重写的章节号（从 1 开始）"),
    hint: str = typer.Option("", "--hint", help="额外写作要求，例如「重点是任务接管」"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
) -> None:
    """只重写某一节，然后重跑质检和渲染。其余章节不动。"""
    try:
        job = _read_job(job_dir)
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    reason = llm_unavailable(cfg)
    if reason:
        console.print(f"[red]{reason}[/]")
        raise typer.Exit(1)

    console.print(f"[cyan]重写第 {section} 节[/]")
    if hint.strip():
        console.print(f"[dim]额外要求：{escape(hint.strip())}[/]")

    try:
        with console.status(f"[cyan]正在重写第 {section} 节[/]") as status:

            def progress(message: str) -> None:
                status.update(f"[cyan]{escape(message)}[/]")

            revision = regenerate_section(
                job,
                job_dir,
                cfg,
                section_index=section,
                hint=hint,
                progress=progress,
            )
    except Video2DocError as exc:
        console.print()
        exc.render(console)
        raise typer.Exit(1) from None

    old = revision.previous
    written = revision.section
    title_change = f" → {written.title}" if old is None or old.title != written.title else ""
    console.print(
        f"[green]✓[/] 第 {section} 节已重写{escape(title_change)} · "
        f"{written.chars} 字 · {len(written.images)} 张图"
    )
    console.print()

    _run_pipeline(job, job_dir, cfg, force=False, from_stage="critique")


@app.command()
def render(
    job_dir: Path = typer.Argument(..., help="任务目录（包含 job.json）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
) -> None:
    """应用 overrides.json 重新生成教程，不调用模型。"""
    try:
        job = _read_job(job_dir)
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    overrides = job_dir / "overrides.json"
    if overrides.exists():
        console.print(f"[dim]使用覆盖层：{escape(str(overrides))}[/]")
    else:
        console.print(f"[dim]没有 {overrides.name}，按模型生成的内容重新渲染[/]")

    _run_pipeline(job, job_dir, cfg, force=False, from_stage="render")


@app.command()
def review(
    job_dir: Path = typer.Argument(..., help="任务目录（包含 job.json）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
    port: int = typer.Option(0, "--port", "-p", help="监听端口，0 表示随机分配"),
    no_open: bool = typer.Option(False, "--no-open", help="不自动打开浏览器"),
) -> None:
    """在浏览器里校对成稿：改标题、正文、配图，保存即重新渲染。"""
    try:
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    try:
        serve_review(job_dir, cfg, console, port=port, open_browser=not no_open)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None


# ------------------------------------------------------------------- batch


@app.command()
def batch(
    targets: list[str] | None = typer.Argument(
        None, help="视频链接或本地文件，可传多个；也可以只用 --file"
    ),
    file: Path | None = typer.Option(None, "--file", "-f", help="每行一个链接，# 开头为注释"),
    out: Path = typer.Option(Path("output"), "--out", "-o", help="输出根目录"),
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
    skip_done: bool = typer.Option(False, "--skip-done", help="跳过已生成教程的任务"),
    stop_on_error: bool = typer.Option(False, "--stop-on-error", help="遇到失败立刻停下"),
    from_stage: str | None = typer.Option(None, "--from", help="从某个阶段开始强制重跑"),
    force: bool = typer.Option(False, "--force", help="忽略缓存，全部重跑"),
) -> None:
    """批量处理多条视频。串行执行，单条失败不影响其他。"""
    try:
        items = _read_targets(targets or [], file)
        cfg = load_config(config)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    if not items:
        console.print("[red]没有可处理的链接。[/]")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]{len(items)} 条任务[/] · 输出到 {escape(str(out))}\n"
            f"[dim]串行执行 · ASR={cfg.asr.provider}/{cfg.asr.model}[/]",
            title=f"[cyan]video2doc {__version__} batch[/]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    results: list[tuple[str, str, str]] = []  # (status, target, detail)
    total_usage = UsageStats()
    interrupted = False

    for index, target in enumerate(items, 1):
        console.rule(f"[bold]{index}/{len(items)}[/] {escape(target)}", align="left")
        job_dir = job_dir_for(out, derive_job_id(target))

        if skip_done and (job_dir / "tutorial.html").exists():
            console.print("[dim]跳过：已经有教程[/]")
            results.append(("skipped", target, "已有教程"))
            continue

        try:
            job, created = open_job(job_dir, target, cfg)
        except Video2DocError as exc:
            exc.render(console)
            results.append(("failed", target, exc.message))
            if stop_on_error:
                break
            continue

        if not created:
            console.print(f"[dim]继续任务 · {escape(str(job_dir))}[/]")

        before = job.usage.model_copy()
        try:
            ok, reason = _run_job(job, job_dir, cfg, force=force, from_stage=from_stage)
        except KeyboardInterrupt:
            console.print("[yellow]已中断。[/]")
            interrupted = True
            results.append(("failed", target, "用户中断"))
            break

        total_usage.calls += job.usage.calls - before.calls
        total_usage.prompt_tokens += job.usage.prompt_tokens - before.prompt_tokens
        total_usage.completion_tokens += job.usage.completion_tokens - before.completion_tokens

        results.append(("ok" if ok else "failed", target, reason or str(job_dir)))
        if not ok and stop_on_error:
            console.print("[yellow]--stop-on-error：停止后续任务[/]")
            break

    _print_batch_summary(results, total_usage)

    failed = [item for item in results if item[0] == "failed"]
    if interrupted:
        raise typer.Exit(130)
    if failed:
        raise typer.Exit(1)


def _read_targets(args: list[str], file: Path | None) -> list[str]:
    """合并命令行参数和文件内容，去掉注释、空行和重复项（保持顺序）。"""
    raw = list(args)
    if file is not None:
        if not file.exists():
            raise Video2DocError(f"找不到链接文件：{file}")
        for line in file.read_text(encoding="utf-8").splitlines():
            text = line.split("#", 1)[0].strip()
            if text:
                raw.append(text)

    seen: set[str] = set()
    items: list[str] = []
    for item in raw:
        text = item.strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def _print_batch_summary(
    results: list[tuple[str, str, str]], usage: UsageStats | None = None
) -> None:
    table = Table(title="批量结果", show_lines=False)
    table.add_column("", width=3, justify="center")
    table.add_column("任务", overflow="fold")
    table.add_column("说明", overflow="fold")
    marks = {"ok": "[green]✓[/]", "skipped": "[yellow]–[/]", "failed": "[red]✗[/]"}
    for status, target, detail in results:
        table.add_row(marks.get(status, status), escape(target), escape(detail))

    ok = sum(1 for item in results if item[0] == "ok")
    skipped = sum(1 for item in results if item[0] == "skipped")
    failed = sum(1 for item in results if item[0] == "failed")
    console.print()
    console.print(table)
    console.print(
        f"[bold]{len(results)} 条[/]："
        f"[green]{ok} 成功[/] · [yellow]{skipped} 跳过[/] · [red]{failed} 失败[/]"
    )
    if usage is not None and usage.calls:
        console.print(f"[dim]LLM 合计：{usage.summary()}[/]")


# ------------------------------------------------------------------ status


@app.command()
def status(
    root: Path = typer.Argument(Path("output"), help="输出根目录"),
    failed: bool = typer.Option(False, "--failed", help="只看有失败阶段的任务"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON，便于脚本处理"),
) -> None:
    """列出输出目录里所有任务的状态。"""
    rows = _collect_status(root)
    if failed:
        rows = [row for row in rows if row.failed]

    if as_json:
        import json

        payload = [
            {
                "job_id": row.job_id,
                "title": row.title,
                "progress": row.progress,
                "failed_stage": row.failed,
                "elapsed": round(row.elapsed, 1),
                "tutorial_bytes": row.tutorial,
                "updated_at": row.updated,
            }
            for row in rows
        ]
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    if not rows:
        console.print(f"[dim]{escape(str(root))} 下没有任务。[/]")
        return

    table = Table(title=f"任务 · {root}")
    table.add_column("任务", style="bold")
    table.add_column("进度")
    table.add_column("标题", overflow="fold")
    table.add_column("耗时", justify="right")
    table.add_column("教程", justify="right")
    table.add_column("更新", style="dim")
    for row in rows:
        progress = row.progress
        if row.failed:
            progress = f"[red]{progress}[/]"
        elif progress == "完成":
            progress = "[green]完成[/]"
        size = f"{row.tutorial / 1024:.0f} KB" if row.tutorial else "[dim]—[/]"
        table.add_row(
            escape(row.job_id),
            progress,
            escape(_short(row.title)),
            f"{row.elapsed:.0f}s",
            size,
            row.updated,
        )
    console.print(table)


def _short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class _JobRow:
    job_id: str
    title: str
    progress: str
    failed: str | None
    elapsed: float
    tutorial: int
    updated: str


def _collect_status(root: Path) -> list[_JobRow]:
    names = [stage.name for stage in default_stages()]
    if not root.exists():
        return []

    rows: list[_JobRow] = []
    for entry in root.iterdir():
        job_path = entry / "job.json"
        if not entry.is_dir() or not job_path.exists():
            continue
        try:
            job = Job.model_validate_json(job_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        failed = next(
            (record.name for record in job.stages.values() if record.status == "failed"),
            None,
        )
        tutorial = entry / "tutorial.html"
        if failed:
            progress = f"✗ {failed}"
        elif tutorial.exists():
            progress = "完成"
        else:
            done = sum(
                1
                for name in names
                if (record := job.stages.get(name)) is not None
                and record.status in ("done", "skipped")
            )
            progress = f"{done}/{len(names)}"

        rows.append(
            _JobRow(
                job_id=job.job_id,
                title=job.source.title if job.source else job.target,
                progress=progress,
                failed=failed,
                elapsed=sum(record.elapsed for record in job.stages.values()),
                tutorial=tutorial.stat().st_size if tutorial.exists() else 0,
                updated=(job.updated_at or "")[:16].replace("T", " "),
            )
        )

    rows.sort(key=lambda row: (row.updated, row.job_id), reverse=True)
    return rows


# -------------------------------------------------------------------- eval


@app.command("eval")
def eval_command(
    job_dir: Path | None = typer.Argument(None, help="任务目录（包含 job.json）"),
    all_jobs: bool = typer.Option(False, "--all", help="评测 --root 下的所有任务"),
    root: Path = typer.Option(Path("output"), "--root", help="配合 --all 的输出根目录"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON，便于脚本处理"),
    strict: bool = typer.Option(False, "--strict", help="有警告时返回非零退出码"),
) -> None:
    """检查成稿的时间戳、章节、配图、OCR、质检和用量。"""
    try:
        if all_jobs:
            reports = _collect_reports(root)
        elif job_dir is not None:
            reports = [evaluate_job(job_dir)]
        else:
            console.print("[red]指定任务目录，或加 --all 评测整个输出目录。[/]")
            raise typer.Exit(1)
    except Video2DocError as exc:
        exc.render(console)
        raise typer.Exit(1) from None

    if not reports:
        console.print(f"[dim]{escape(str(root))} 下没有可评测的任务。[/]")
        return

    if as_json:
        import json

        payload = (
            reports[0].model_dump(mode="json")
            if len(reports) == 1 and not all_jobs
            else [report.model_dump(mode="json") for report in reports]
        )
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        for index, report in enumerate(reports):
            if index:
                console.print()
            _print_eval_report(report)

    if strict and any(not report.passed for report in reports):
        raise typer.Exit(1)


def _collect_reports(root: Path) -> list[EvalReport]:
    if not root.exists():
        console.print(f"[red]目录不存在：{escape(str(root))}[/]")
        return []

    reports: list[EvalReport] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(("_", ".")):
            # 备份 / 临时目录不参与批量评测
            continue
        if not entry.is_dir() or not (entry / "job.json").exists():
            continue
        try:
            reports.append(evaluate_job(entry))
        except Video2DocError as exc:
            console.print(f"[yellow]跳过 {escape(entry.name)}：{escape(exc.message)}[/]")
    return reports


def _print_eval_report(report: EvalReport) -> None:
    console.print(
        Panel(
            f"[bold]{escape(report.title)}[/]\n"
            f"[dim]{hms(report.duration)} · {report.sections} 节 · "
            f"{report.chars} 字 · {report.images} 图 · "
            f"HTML {report.html_bytes / 1024:.0f} KB[/]",
            border_style="green" if report.passed else "yellow",
            padding=(0, 1),
        )
    )
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", width=12)
    table.add_column()
    table.add_row(
        "时间戳",
        f"{report.timestamp_errors} 错 · 章节覆盖 {report.time_coverage:.0%}",
    )
    average = round(report.chars / report.sections) if report.sections else 0
    table.add_row(
        "章节",
        f"空 {report.empty_sections} · 短 {report.short_sections} · "
        f"长 {report.long_sections} · 平均 {average} 字",
    )
    table.add_row(
        "配图",
        f"缺失 {report.missing_image_refs} · 未用 {report.unused_images} · "
        f"重复 {report.duplicate_images} · HTML figure {report.figures}",
    )
    table.add_row(
        "OCR",
        f"{report.ocr_text_frames}/{report.ocr_frames} 帧有字 · {report.ocr_chars} 字 · "
        f"代码画面 {report.code_frames} · 正文代码块 {report.code_blocks}",
    )
    if report.transcript_utterances:
        table.add_row(
            "转写",
            f"{report.transcript_utterances} 句 · {report.transcript_chars} 字 · "
            f"覆盖 {report.transcript_coverage:.0%}",
        )
    if report.critique_issues or report.critique_coverage:
        table.add_row(
            "质检",
            f"{report.critique_issues} 条（{report.critique_high} 高）· "
            f"覆盖 {report.critique_coverage:.0%} · 重写 {report.rewrites} 节",
        )
    table.add_row(
        "LLM",
        f"{report.llm_calls} 次 · {report.llm_prompt_tokens:,} 输入 / "
        f"{report.llm_completion_tokens:,} 输出 · 总耗时 {report.total_elapsed:.0f}s",
    )
    console.print(table)

    if report.notes:
        console.print("[dim]" + "；".join(escape(note) for note in report.notes) + "[/]")
    if report.warnings:
        console.print("[yellow]警告[/]")
        for warning in report.warnings:
            console.print(f"  [yellow]•[/] {escape(warning)}")
    else:
        console.print("[green]✓ 结构检查通过[/]")


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
        + (f" [green]已校正 {transcript.corrections} 处[/]" if transcript.corrections else "")
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
    try:
        ok, _ = _run_job(job, job_dir, cfg, force=force, from_stage=from_stage)
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断。任务已保存，重跑同一条命令即可继续。[/]")
        raise typer.Exit(130) from None
    if not ok:
        raise typer.Exit(1)


def _run_job(
    job: Job,
    job_dir: Path,
    cfg: Config,
    *,
    force: bool,
    from_stage: str | None,
) -> tuple[bool, str]:
    """跑完一个任务。返回 (是否成功, 失败原因)，不抛退出码，方便 batch 复用。"""
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
        return False, exc.message

    _print_summary(job, job_dir)
    return True, ""


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

    if job.usage.calls:
        table.add_row("LLM 用量", job.usage.summary())

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

"""ocr 阶段：把选中画面里的文字读出来。

它和 frames 分开，是因为 OCR 引擎是可选的：
没装就跳过，后面的对齐仍然能只用口播文本工作，只是少一维信息。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..providers.ocr import create_ocr, resolve_name
from ..schema import FrameCandidate, FrameSelection, FrameText, OCRResult
from ..utils.checkpoint import discard, load_entries, save_entries
from ..utils.common import fingerprint


class OCRStage(Stage):
    name = "ocr"
    title = "识别画面文字"
    version = 2
    requires = ("frames",)

    def params(self, config: Config) -> dict:
        return config.ocr.model_dump()

    def skip(self, ctx: Context) -> str | None:
        resolved = resolve_name(ctx.config.ocr.provider)
        if resolved == "none":
            return '未安装 OCR 引擎（pip install "video2doc[ocr]"）'
        frames = ctx.load("frames", FrameSelection)
        if not frames.frames:
            return "没有可用画面"
        return None

    def run(self, ctx: Context) -> BaseModel:
        frames = ctx.load("frames", FrameSelection)
        config = ctx.config.ocr
        provider = create_ocr(config)
        total = len(frames.frames)
        checkpoint = ctx.path("cache", "ocr_frames.json")
        saved = load_entries(checkpoint)
        base_key = {"provider": provider.name, "config": config.model_dump(mode="json")}

        results: dict[int, FrameText] = {}
        pending: list[tuple[FrameCandidate, Path, str]] = []
        for frame in frames.frames:
            path = ctx.job_dir / frame.path
            key = _frame_key(base_key, path)
            cached = saved.get(str(frame.index))
            if cached and cached.get("key") == key:
                try:
                    results[frame.index] = FrameText.model_validate(cached.get("data"))
                    continue
                except (TypeError, ValueError):
                    pass
            pending.append((frame, path, key))

        if results:
            ctx.progress(f"复用 OCR 检查点 {len(results)}/{total} 张…")

        workers = min(max(1, config.workers), len(pending)) if pending else 1
        if not provider.parallel_safe:
            workers = 1
        if workers > 1:
            provider.warmup()

        def read_frame(item: tuple[FrameCandidate, Path, str]) -> tuple[str, FrameText]:
            frame, path, key = item
            blocks = [
                block for block in provider.read(path) if block.confidence >= config.min_confidence
            ]
            text = "\n".join(block.text for block in blocks)
            confidences = [block.confidence for block in blocks]
            return key, FrameText(
                index=frame.index,
                text=text,
                blocks=blocks,
                char_count=len(text.replace("\n", "")),
                mean_confidence=(
                    round(sum(confidences) / len(confidences), 4) if confidences else None
                ),
            )

        def store(key: str, frame_text: FrameText, done: int) -> None:
            results[frame_text.index] = frame_text
            saved[str(frame_text.index)] = {"key": key, "data": frame_text.model_dump(mode="json")}
            save_entries(checkpoint, saved)
            ctx.progress(f"识别画面文字 {done}/{total}…")

        done = len(results)
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(read_frame, item) for item in pending]
                for future in as_completed(futures):
                    key, frame_text = future.result()
                    done += 1
                    store(key, frame_text, done)
        else:
            for item in pending:
                key, frame_text = read_frame(item)
                done += 1
                store(key, frame_text, done)

        ordered = [results[frame.index] for frame in frames.frames]
        discard(checkpoint)
        return OCRResult(provider=provider.name, language=config.language, frames=ordered)

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, OCRResult)
        return result.summary()


def _frame_key(base: dict, path: Path) -> str:
    try:
        stat = path.stat()
        file_key = f"{path.as_posix()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        file_key = path.as_posix()
    return fingerprint({**base, "file": file_key})

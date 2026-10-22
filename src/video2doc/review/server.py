"""本地校对界面：一个只监听 127.0.0.1 的小服务 + 原生 JS 页面。

设计原则：
- 不引入前端框架、不联网、不装新依赖（标准库 + 已有的 markdown-it）；
- 页面只负责编辑，所有落盘和渲染都在服务端串行执行；
- 编辑结果仍然写进 `overrides.json`，和手工改文件完全等价。
"""

from __future__ import annotations

import io
import json
import mimetypes
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from rich.console import Console
from rich.markup import escape

from ..config import Config
from ..errors import StageError, Video2DocError
from ..overrides import (
    OVERRIDES_NAME,
    Overrides,
    SectionOverride,
    apply_overrides,
    load_overrides,
    save_overrides,
)
from ..pipeline import Pipeline
from ..render import render_fragment
from ..revision import regenerate_section
from ..schema import Document, ImageRef, Job, SourceInfo
from ..stages import CritiqueStage, RenderStage
from ..utils.common import hms
from ..writing import llm_unavailable

COOKIE_NAME = "v2doc_review"
MAX_BODY = 8 * 1024 * 1024
STATIC_FILES = ("index.html", "app.css", "app.js")


class ReviewError(Video2DocError):
    """校对界面的输入问题，返回给前端而不是打断服务。"""


class ReviewApp:
    def __init__(
        self,
        job_dir: Path,
        config: Config,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        open_browser: bool = True,
        console: Console | None = None,
    ) -> None:
        self.job_dir = job_dir.resolve()
        self.config = config
        self.console = console or Console()
        self.open_browser = open_browser
        self.token = secrets.token_urlsafe(18)
        self.lock = threading.Lock()

        if not (self.job_dir / "job.json").exists():
            raise StageError(
                f"找不到任务文件：{self.job_dir / 'job.json'}",
                hint="先运行 v2doc run，或把任务目录指对。",
            )
        self._check_ready()

        try:
            self._server = _ReviewServer((host, port), _Handler)
        except OSError as exc:
            raise StageError(
                f"端口 {port or '(自动)'} 起不来：{exc}",
                hint="换一个端口试试：v2doc review <任务目录> --port 8765",
            ) from exc
        self._server.app = self
        self.host = host
        self.port = self._server.server_port
        self.url = f"http://{host}:{self.port}/?token={self.token}"

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> str:
        threading.Thread(
            target=self._server.serve_forever, name="v2doc-review", daemon=True
        ).start()
        if self.open_browser:
            webbrowser.open(self.url)
        return self.url

    def serve_forever(self) -> None:
        """阻塞运行，Ctrl+C 退出。"""
        if self.open_browser:
            webbrowser.open(self.url)
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ------------------------------------------------------------------ 数据

    def _check_ready(self) -> None:
        job = _load_job(self.job_dir)
        record = job.stages.get("write")
        if record is None or record.status != "done" or not record.output:
            raise StageError(
                "这个任务还没有正文，无法校对",
                hint="先跑一次完整的 v2doc run（需要 write 阶段完成）。",
            )
        if not (self.job_dir / record.output).exists():
            raise StageError(
                f"正文文件丢失：{record.output}",
                hint="加上 --from write 重新生成正文。",
            )

    def state(self) -> dict[str, Any]:
        with self.lock:
            return self._state()

    def _state(self) -> dict[str, Any]:
        job = _load_job(self.job_dir)
        document = _load_document(job, self.job_dir)
        overrides = load_overrides(self.job_dir) or Overrides()
        effective, warnings = apply_overrides(document, overrides)
        source = _load_optional(job, self.job_dir, "ingest", SourceInfo)
        tutorial = self.job_dir / "tutorial.html"
        base_sections = {section.index: section for section in document.sections}

        sections = []
        for section in effective.sections:
            original = base_sections.get(section.index)
            sections.append(
                {
                    "index": section.index,
                    "title": section.title,
                    "model_title": original.title if original else section.title,
                    "kind": section.kind,
                    "start": section.start,
                    "end": section.end,
                    "start_hms": hms(section.start),
                    "end_hms": hms(section.end),
                    "chars": section.chars,
                    "markdown": section.markdown,
                    "rewritten": section.rewritten,
                    "overridden": section.index in overrides.sections,
                    "images": [_image_payload(ref) for ref in section.images],
                }
            )

        pool = [
            {**_image_payload(ref), "section": section.index}
            for section in document.sections
            for ref in section.images
        ]

        return {
            "job_id": job.job_id,
            "job_dir": str(self.job_dir),
            "source_title": (source.title if source else "") or job.target,
            "title": effective.title,
            "model_title": document.title,
            "title_locked": self.config.render.title,
            "intro": effective.intro,
            "model_intro": document.intro,
            "sections": sections,
            "pool": pool,
            "overrides_count": len(overrides.sections),
            "warnings": warnings,
            "tutorial": {
                "exists": tutorial.exists(),
                "size": tutorial.stat().st_size if tutorial.exists() else 0,
                "updated": (
                    time.strftime("%H:%M:%S", time.localtime(tutorial.stat().st_mtime))
                    if tutorial.exists()
                    else ""
                ),
            },
            "usage": job.usage.model_dump(),
        }

    # ---------------------------------------------------------------- 写操作

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            job = _load_job(self.job_dir)
            base = _load_document(job, self.job_dir)
            overrides = _diff_overrides(base, payload)
            path = self.job_dir / OVERRIDES_NAME
            if overrides.is_empty():
                path.unlink(missing_ok=True)
            else:
                save_overrides(self.job_dir, overrides)
            result = self._render()
            state = self._state()
        state["render"] = result
        return state

    def rewrite(self, payload: dict[str, Any]) -> dict[str, Any]:
        reason = llm_unavailable(self.config)
        if reason:
            raise ReviewError(reason, "配好 LLM 再试，或者直接手改正文。")
        try:
            section = int(payload.get("section") or 0)
        except (TypeError, ValueError) as exc:
            raise ReviewError("章节号不合法") from exc
        if section < 1:
            raise ReviewError("章节号不合法")

        hint = str(payload.get("hint") or "").strip()
        with self.lock:
            job = _load_job(self.job_dir)
            revision = regenerate_section(
                job, self.job_dir, self.config, section_index=section, hint=hint
            )
            # 这一节已经是模型新写的了，旧的覆盖再留着就看不到新内容
            self._clear_section_override(section)
            result = self._render(critique=True)
            state = self._state()

        state["render"] = result
        state["rewrite"] = {
            "section": section,
            "title": revision.section.title,
            "previous_title": revision.previous.title if revision.previous else "",
            "chars": revision.section.chars,
            "delta": revision.delta_chars,
        }
        return state

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = _load_job(self.job_dir)
        document = _load_document(job, self.job_dir)
        known = _image_index(document)
        refs: list[ImageRef] = []
        for path in payload.get("images") or []:
            if not isinstance(path, str):
                continue
            ref = known.get(path) or known.get(Path(path).name)
            if ref is not None:
                refs.append(ref.model_copy(update={"id": len(refs) + 1}))
        config = self.config.render.model_copy(update={"embed_images": False})
        html = render_fragment(
            str(payload.get("markdown") or ""),
            refs,
            job_dir=self.job_dir,
            link=None,
            config=config,
            title=str(payload.get("title") or ""),
        )
        return {"html": html}

    def _render(self, *, critique: bool = False) -> dict[str, Any]:
        job = _load_job(self.job_dir)
        buffer = io.StringIO()
        quiet = Console(file=buffer, width=110, force_terminal=False, color_system=None)
        # 只跑渲染（重写时顺带重跑一次质检），不碰前面那些昂贵的阶段
        stages = [CritiqueStage(), RenderStage()] if critique else [RenderStage()]
        started = time.perf_counter()
        Pipeline(
            stages,
            job,
            self.job_dir,
            self.config,
            quiet,
            force=True,
        ).run()
        elapsed = time.perf_counter() - started
        tutorial = self.job_dir / "tutorial.html"
        size = tutorial.stat().st_size if tutorial.exists() else 0
        log = [line.strip() for line in buffer.getvalue().splitlines() if line.strip()]
        return {
            "elapsed": round(elapsed, 2),
            "size": size,
            "stage": "critique+render" if critique else "render",
            "log": log[-8:],
        }

    def _clear_section_override(self, section: int) -> None:
        overrides = load_overrides(self.job_dir)
        if overrides is None or section not in overrides.sections:
            return
        overrides.sections.pop(section)
        path = self.job_dir / OVERRIDES_NAME
        if overrides.is_empty():
            path.unlink(missing_ok=True)
        else:
            save_overrides(self.job_dir, overrides)

    # ------------------------------------------------------------------ 静态

    def static(self, name: str) -> bytes:
        if name not in STATIC_FILES:
            raise ReviewError("未知静态文件")
        return (Path(__file__).with_name("static") / name).read_bytes()

    def frame(self, raw_name: str) -> Path:
        name = unquote(raw_name)
        frames = (self.job_dir / "frames").resolve()
        target = (frames / name).resolve()
        if not target.is_relative_to(frames) or not target.is_file():
            raise ReviewError("找不到画面文件")
        return target


def serve(
    job_dir: Path,
    config: Config,
    console: Console,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> None:
    app = ReviewApp(job_dir, config, port=port, open_browser=open_browser, console=console)
    console.print()
    console.print(f"[bold cyan]校对界面[/]  {escape(app.job_dir.name)}")
    console.print(f"  [bold]{app.url}[/]")
    console.print("  [dim]Ctrl+C 停止 · 编辑结果写入 overrides.json[/]")
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]校对服务已停止。[/]")
    finally:
        app.stop()


# ------------------------------------------------------------------ HTTP


class _ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    app: ReviewApp

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        # 浏览器关页面时会断开连接，不必打印堆栈
        import sys

        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, BrokenPipeError, OSError)):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server: _ReviewServer
    server_version = "v2doc-review"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    # ------------------------------------------------------------- dispatch

    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _guard(self, handler) -> None:  # noqa: ANN001
        try:
            handler()
        except Video2DocError as exc:
            self.server.app.console.print(f"[red]校对界面：{exc.message}[/]")
            self._json({"error": exc.message, "hint": exc.hint}, status=400)
        except (ConnectionError, BrokenPipeError):
            return
        except Exception as exc:  # noqa: BLE001
            self.server.app.console.print_exception()
            self._json(
                {"error": f"{type(exc).__name__}: {exc}", "hint": "把服务端的报错贴到 issue。"},
                status=500,
            )

    # ------------------------------------------------------------------ GET

    def _get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authorized():
            self._json(
                {"error": "无效的访问令牌", "hint": "请使用 v2doc review 打印出来的完整地址。"},
                status=403,
            )
            return

        if path in ("/", "/index.html"):
            self._bytes(self.server.app.static("index.html"), "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            name = path[len("/static/") :]
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            self._bytes(self.server.app.static(name), content_type + "; charset=utf-8")
        elif path.startswith("/frames/"):
            target = self.server.app.frame(path[len("/frames/") :])
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._bytes(target.read_bytes(), content_type)
        elif path == "/tutorial.html":
            tutorial = self.server.app.job_dir / "tutorial.html"
            if not tutorial.exists():
                self._json({"error": "还没有生成教程"}, status=404)
                return
            self._bytes(tutorial.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self.server.app.state())
        else:
            self._json({"error": "not found"}, status=404)

    # ----------------------------------------------------------------- POST

    def _post(self) -> None:
        if not self._authorized():
            self._json({"error": "无效的访问令牌"}, status=403)
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json({"error": "请求体太大"}, status=413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "请求不是合法 JSON"}, status=400)
            return
        if not isinstance(payload, dict):
            self._json({"error": "请求体必须是 JSON 对象"}, status=400)
            return

        path = urlparse(self.path).path
        if path == "/api/save":
            self._json(self.server.app.save(payload))
        elif path == "/api/preview":
            self._json(self.server.app.preview(payload))
        elif path == "/api/rewrite":
            self._json(self.server.app.rewrite(payload))
        else:
            self._json({"error": "not found"}, status=404)

    # -------------------------------------------------------------- helpers

    def _authorized(self) -> bool:
        app = self.server.app
        query = parse_qs(urlparse(self.path).query)
        token = query.get("token", [""])[0]
        if token and secrets.compare_digest(token, app.token):
            # 第一次带 token 打开页面时把凭据记到 cookie，之后图片和 API 都靠它
            self._cookie = True
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and secrets.compare_digest(value, app.token):
                return True
        return False

    def _json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(data, "application/json; charset=utf-8", status=status)

    def _bytes(self, data: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if getattr(self, "_cookie", False):
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self.server.app.token}; Path=/; SameSite=Lax; HttpOnly",
            )
            self._cookie = False
        self.end_headers()
        self.wfile.write(data)


# ------------------------------------------------------------------ 数据工具


def _load_job(job_dir: Path) -> Job:
    path = job_dir / "job.json"
    if not path.exists():
        raise StageError(f"找不到任务文件：{path}", hint="检查任务目录是否正确。")
    return Job.model_validate_json(path.read_text(encoding="utf-8"))


def _load_document(job: Job, job_dir: Path) -> Document:
    record = job.stages.get("write")
    if record is None or record.status != "done" or not record.output:
        raise StageError("这个任务还没有正文", hint="先跑一次完整的 v2doc run。")
    path = job_dir / record.output
    if not path.exists():
        raise StageError(f"正文文件丢失：{path}", hint="加上 --from write 重新生成。")
    return Document.model_validate_json(path.read_text(encoding="utf-8"))


def _load_optional(job: Job, job_dir: Path, name: str, model: type[Any]) -> Any:
    record = job.stages.get(name)
    if record is None or record.status != "done" or not record.output:
        return None
    path = job_dir / record.output
    if not path.exists():
        return None
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _image_payload(ref: ImageRef) -> dict[str, Any]:
    return {
        "id": ref.id,
        "image": ref.image,
        "url": f"/frames/{Path(ref.image).name}",
        "time": ref.time,
        "hms": hms(ref.time),
        "caption": ref.caption,
        "kind": ref.kind,
    }


def _image_index(document: Document) -> dict[str, ImageRef]:
    known: dict[str, ImageRef] = {}
    for section in document.sections:
        for ref in section.images:
            known[ref.image] = ref
            known.setdefault(Path(ref.image).name, ref)
    return known


def _diff_overrides(base: Document, payload: dict[str, Any]) -> Overrides:
    """把界面提交的内容和模型原稿比对，只保存真正改过的字段。"""
    overrides = Overrides()

    title = str(payload.get("title") or "").strip()
    if title and title != base.title:
        overrides.title = title

    intro = payload.get("intro")
    if isinstance(intro, str) and intro.strip() != base.intro.strip():
        overrides.intro = intro.strip()

    original = {section.index: section for section in base.sections}
    for item in payload.get("sections") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        source = original.get(index)
        if source is None:
            continue

        change = SectionOverride()
        section_title = str(item.get("title") or "").strip()
        if section_title and section_title != source.title:
            change.title = section_title

        markdown = item.get("markdown")
        if isinstance(markdown, str) and markdown.strip() != source.markdown.strip():
            change.markdown = markdown.strip()

        images = item.get("images")
        if isinstance(images, list):
            paths = [str(path) for path in images if isinstance(path, str)]
            if paths != [ref.image for ref in source.images]:
                change.images = paths

        if change.title is not None or change.markdown is not None or change.images is not None:
            overrides.sections[index] = change

    return overrides

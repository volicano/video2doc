from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from video2doc.config import Config, RenderConfig
from video2doc.review import ReviewApp, ReviewError
from video2doc.revision import Revision
from video2doc.schema import (
    Document,
    ImageRef,
    Job,
    SourceInfo,
    StageRecord,
    WrittenSection,
)
from video2doc.utils.common import now_iso


def _write_json(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _document() -> Document:
    return Document(
        title="模型标题",
        intro="模型导语",
        duration=200.0,
        chars=24,
        images=2,
        sections=[
            WrittenSection(
                index=1,
                title="第一节",
                start=0.0,
                end=100.0,
                markdown="## 要点\n第一节正文。\n\n[[img:1]]",
                chars=13,
                images=[
                    ImageRef(id=1, time=10.0, image="frames/f01.jpg", caption="第一页"),
                ],
            ),
            WrittenSection(
                index=2,
                title="第二节",
                start=100.0,
                end=200.0,
                markdown="## 演示\n第二节正文。\n\n[[img:1]]",
                chars=13,
                images=[
                    ImageRef(id=1, time=120.0, image="frames/f02.jpg", caption="第二页"),
                ],
            ),
        ],
    )


def _make_job(job_dir: Path) -> Job:
    frames = job_dir / "frames"
    frames.mkdir(parents=True)
    Image.new("RGB", (320, 180), (20, 40, 80)).save(frames / "f01.jpg", "JPEG")
    Image.new("RGB", (320, 180), (80, 40, 20)).save(frames / "f02.jpg", "JPEG")

    _write_json(job_dir / "stages" / "write.json", _document())
    _write_json(
        job_dir / "stages" / "ingest.json",
        SourceInfo(kind="file", path="x.mp4", title="测试视频", duration=200.0),
    )

    job = Job(job_id="t", target="x.mp4", created_at=now_iso(), updated_at=now_iso())
    job.stages["ingest"] = StageRecord(
        name="ingest", status="done", fingerprint="x", output="stages/ingest.json", summary="fake"
    )
    job.stages["write"] = StageRecord(
        name="write", status="done", fingerprint="x", output="stages/write.json", summary="fake"
    )
    (job_dir / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
    return job


@pytest.fixture
def review(tmp_path: Path):
    _make_job(tmp_path)
    app = ReviewApp(
        tmp_path,
        Config(render=RenderConfig(embed_images=True)),
        open_browser=False,
    )
    app.start()
    try:
        yield app
    finally:
        app.stop()


@pytest.fixture
def client(review: ReviewApp):
    with httpx.Client(base_url=f"http://127.0.0.1:{review.port}", timeout=60) as http:
        # 第一次带 token 打开页面：服务端会种下 cookie，后续请求靠它鉴权
        response = http.get(f"/?token={review.token}")
        assert response.status_code == 200
        yield http


def test_requires_token(review: ReviewApp) -> None:
    with httpx.Client(base_url=f"http://127.0.0.1:{review.port}", timeout=30) as http:
        assert http.get("/api/state").status_code == 403
        assert http.get("/frames/f01.jpg").status_code == 403
        assert http.get("/?token=wrong").status_code == 403


def test_state_lists_sections_and_images(client: httpx.Client, review: ReviewApp) -> None:
    data = client.get("/api/state").json()

    assert data["job_id"] == "t"
    assert data["source_title"] == "测试视频"
    assert data["title"] == "模型标题"
    assert [section["index"] for section in data["sections"]] == [1, 2]
    assert data["sections"][0]["images"][0]["url"] == "/frames/f01.jpg"
    assert data["sections"][0]["start_hms"] == "00:00"
    assert data["pool"][1]["section"] == 2
    assert data["tutorial"]["exists"] is False
    assert data["warnings"] == []


def test_save_writes_minimal_overrides_and_rerenders(
    client: httpx.Client, review: ReviewApp, tmp_path: Path
) -> None:
    data = client.get("/api/state").json()
    before = hashlib.sha256(
        (tmp_path / "stages" / "write.json").read_bytes()
    ).hexdigest()

    payload = {
        "title": "人工标题",
        "intro": "人工导语",
        "sections": [
            {
                "index": 1,
                "title": "人工第一节",
                "markdown": "## 改写\n这是人工改过的正文。\n\n[[img:1]]",
                "images": ["frames/f02.jpg", "frames/f01.jpg"],
            },
            {
                "index": 2,
                "title": data["sections"][1]["title"],
                "markdown": data["sections"][1]["markdown"],
                "images": ["frames/f02.jpg"],
            },
        ],
    }
    response = client.post("/api/save", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()

    assert saved["title"] == "人工标题"
    assert saved["render"]["size"] > 0
    assert saved["tutorial"]["exists"] is True
    assert saved["sections"][0]["images"][0]["image"] == "frames/f02.jpg"

    overrides = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
    assert overrides["title"] == "人工标题"
    assert overrides["intro"] == "人工导语"
    assert overrides["sections"]["1"]["title"] == "人工第一节"
    assert overrides["sections"]["1"]["images"] == ["frames/f02.jpg", "frames/f01.jpg"]
    # 第 2 节没有改动，不该写进覆盖层
    assert "2" not in overrides["sections"]

    html = (tmp_path / "tutorial.html").read_text(encoding="utf-8")
    assert "人工标题" in html
    assert "这是人工改过的正文。" in html

    after = hashlib.sha256((tmp_path / "stages" / "write.json").read_bytes()).hexdigest()
    assert after == before, "保存只该写 overrides.json，不能回头改模型原稿"

    # 改回模型原稿：覆盖层应当被清空并删除
    base = json.loads((tmp_path / "stages" / "write.json").read_text(encoding="utf-8"))
    revert = {
        "title": base["title"],
        "intro": base["intro"],
        "sections": [
            {
                "index": section["index"],
                "title": section["title"],
                "markdown": section["markdown"],
                "images": [ref["image"] for ref in section["images"]],
            }
            for section in base["sections"]
        ],
    }
    response = client.post("/api/save", json=revert)
    assert response.status_code == 200, response.text
    assert not (tmp_path / "overrides.json").exists()
    assert response.json()["warnings"] == []


def test_preview_renders_markdown_with_images(client: httpx.Client) -> None:
    response = client.post(
        "/api/preview",
        json={
            "markdown": "## 小标题\n\n正文。\n\n[[img:1]]",
            "images": ["frames/f01.jpg"],
            "title": "第一节",
        },
    )
    assert response.status_code == 200, response.text
    html = response.json()["html"]
    assert "<h3>小标题</h3>" in html
    assert "<figure>" in html
    assert 'src="frames/f01.jpg"' in html


def test_frame_route_blocks_traversal(client: httpx.Client) -> None:
    ok = client.get("/frames/f01.jpg")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/jpeg")

    for attack in ("../job.json", "..%2Fjob.json", "..%2F..%2Fjob.json"):
        response = client.get(f"/frames/{attack}")
        assert response.status_code in (400, 404), attack


def test_review_error_is_reported_as_json(review: ReviewApp) -> None:
    error = ReviewError("输入不对", "改一下")
    assert error.message == "输入不对"
    assert error.hint == "改一下"


def test_rewrite_returns_new_section_and_rerenders(
    client: httpx.Client, monkeypatch
) -> None:
    document = _document()
    new_section = document.sections[1].model_copy(
        update={"title": "模型重写", "markdown": "## 重写\n新内容。"}
    )

    calls: list[tuple[int, str]] = []

    def fake_regenerate(job, job_dir, config, *, section_index, hint="", progress=None):
        calls.append((section_index, hint))
        return Revision(document=document, section=new_section, previous=document.sections[1])

    rendered: list[bool] = []

    def fake_render(self, *, critique=False):
        rendered.append(critique)
        return {"elapsed": 0.1, "size": 1, "stage": "x", "log": []}

    monkeypatch.setattr("video2doc.review.server.llm_unavailable", lambda config: None)
    monkeypatch.setattr("video2doc.review.server.regenerate_section", fake_regenerate)
    monkeypatch.setattr("video2doc.review.server.ReviewApp._render", fake_render)

    response = client.post("/api/rewrite", json={"section": 2, "hint": "换个说法"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert calls == [(2, "换个说法")]
    assert rendered == [True], "重写后要重跑质检再渲染"
    assert data["rewrite"]["section"] == 2
    assert data["rewrite"]["title"] == "模型重写"


def test_rewrite_without_llm_key_is_rejected(
    client: httpx.Client, monkeypatch
) -> None:
    monkeypatch.setattr(
        "video2doc.review.server.llm_unavailable", lambda config: "未设置 X，跳过写作"
    )
    response = client.post("/api/rewrite", json={"section": 1})
    assert response.status_code == 400
    assert "跳过写作" in response.json()["error"]

from __future__ import annotations

from types import SimpleNamespace

from video2doc.config import Config, NetworkConfig
from video2doc.schema import SourceInfo
from video2doc.stages.ingest import IngestStage, _resolve_douyin_url


class _FakeResponse:
    url = "https://www.douyin.com/video/7298145681699622182?previous_page=web_code_link"
    text = ""


class _FakeClient:
    calls: list[dict] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        _FakeClient.calls.append(kwargs)

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        return _FakeResponse()


class _Ctx:
    def __init__(self, proxy: str | None = None) -> None:
        self.config = Config(network=NetworkConfig(proxy=proxy))
        self.messages: list[str] = []

    def progress(self, message: str) -> None:
        self.messages.append(message)

    def detail(self, message: str) -> None:
        self.messages.append(message)


def test_long_douyin_link_does_not_make_network_call(monkeypatch) -> None:
    def fail(**kwargs: object) -> None:
        raise AssertionError("long link should not be resolved over HTTP")

    monkeypatch.setattr("video2doc.stages.ingest.httpx.Client", fail)
    ctx = _Ctx()
    assert (
        _resolve_douyin_url(ctx, "https://www.iesdouyin.com/share/video/7298145681699622182/")
        == "https://www.douyin.com/video/7298145681699622182"
    )


def test_short_douyin_link_follows_redirect(monkeypatch) -> None:
    _FakeClient.calls.clear()
    monkeypatch.setattr("video2doc.stages.ingest.httpx.Client", _FakeClient)
    ctx = _Ctx(proxy="http://127.0.0.1:10808")
    assert (
        _resolve_douyin_url(ctx, "https://v.douyin.com/iRNBho6u/")
        == "https://www.douyin.com/video/7298145681699622182"
    )
    assert _FakeClient.calls[0]["proxy"] == "http://127.0.0.1:10808"
    assert "解析抖音短链" in ctx.messages[0]


def test_share_text_extracts_url_before_ingest(monkeypatch) -> None:
    captured: list[str] = []

    def fake_ingest(ctx: object, url: str) -> SourceInfo:
        captured.append(url)
        return SourceInfo(kind="url", path="video.mp4", title="test", url=url)

    monkeypatch.setattr("video2doc.stages.ingest._ingest_url", fake_ingest)
    job = SimpleNamespace(target="复制打开抖音 https://v.douyin.com/iRNBho6u/ 看看")
    ctx = SimpleNamespace(job=job, config=Config(), job_dir=None)
    result = IngestStage().run(ctx)

    assert captured == ["https://v.douyin.com/iRNBho6u/"]
    assert result.url == "https://v.douyin.com/iRNBho6u/"
    assert job.source == result

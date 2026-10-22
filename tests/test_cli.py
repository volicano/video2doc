from __future__ import annotations

import json
from pathlib import Path

from video2doc.cli import _collect_status, _read_targets
from video2doc.schema import Job, SourceInfo, StageRecord, UsageStats


def _write_job(directory: Path, job: Job) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")


def _job(job_id: str, *, title: str, updated: str, stages: dict[str, StageRecord]) -> Job:
    return Job(
        job_id=job_id,
        target=f"https://example.com/{job_id}",
        created_at="2026-10-22T09:00:00",
        updated_at=updated,
        source=SourceInfo(kind="url", path="", title=title),
        stages=stages,
    )


def test_read_targets_strips_comments_and_duplicates(tmp_path: Path) -> None:
    file = tmp_path / "urls.txt"
    file.write_text(
        "# 这行是注释\n"
        "https://example.com/a\n"
        "\n"
        "https://example.com/b   # 行尾注释\n"
        "https://example.com/a\n",
        encoding="utf-8",
    )

    items = _read_targets(["https://example.com/b"], file)

    assert items == ["https://example.com/b", "https://example.com/a"]


def test_read_targets_without_file_keeps_order(tmp_path: Path) -> None:
    assert _read_targets(["b", "a", "b"], None) == ["b", "a"]


def test_collect_status_marks_finished_and_failed(tmp_path: Path) -> None:
    done = tmp_path / "BVdone"
    _write_job(
        done,
        _job(
            "BVdone",
            title="已完成的教程",
            updated="2026-10-22T10:00:00",
            stages={
                "ingest": StageRecord(
                    name="ingest", status="done", fingerprint="f1", elapsed=2.0
                )
            },
        ),
    )
    (done / "tutorial.html").write_bytes(b"x" * 2048)

    broken = tmp_path / "BVfailed"
    _write_job(
        broken,
        _job(
            "BVfailed",
            title="失败的教程",
            updated="2026-10-22T11:00:00",
            stages={
                "ingest": StageRecord(
                    name="ingest", status="done", fingerprint="f1", elapsed=3.0
                ),
                "write": StageRecord(
                    name="write",
                    status="failed",
                    fingerprint="f2",
                    error="LLM 返回的不是合法 JSON",
                    elapsed=1.0,
                ),
            },
        ),
    )

    rows = {row.job_id: row for row in _collect_status(tmp_path)}

    assert rows["BVdone"].progress == "完成"
    assert rows["BVdone"].tutorial == 2048
    assert rows["BVdone"].failed is None
    assert rows["BVfailed"].progress == "✗ write"
    assert rows["BVfailed"].failed == "write"
    assert rows["BVfailed"].tutorial == 0
    # 最近更新的排前面
    assert [row.job_id for row in _collect_status(tmp_path)] == ["BVfailed", "BVdone"]


def test_collect_status_ignores_broken_job_json(tmp_path: Path) -> None:
    broken = tmp_path / "BVbad"
    broken.mkdir()
    (broken / "job.json").write_text("{not json", encoding="utf-8")

    assert _collect_status(tmp_path) == []


def test_usage_accumulates_and_is_serialized(tmp_path: Path) -> None:
    usage = UsageStats()
    usage.add(1200, 300)
    usage.add(800, 200)

    assert usage.calls == 2
    assert usage.total_tokens == 2500
    assert "2 次" in usage.summary()

    job = _job("BVusage", title="用量", updated="2026-10-22T10:00:00", stages={})
    job.usage = usage
    path = tmp_path / "job.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["usage"] == {
        "calls": 2,
        "prompt_tokens": 2000,
        "completion_tokens": 500,
    }


def test_old_job_without_usage_field_still_loads() -> None:
    job = Job.model_validate(
        {
            "job_id": "BVold",
            "target": "https://example.com/old",
            "created_at": "2026-10-01T00:00:00",
            "updated_at": "2026-10-01T00:00:00",
        }
    )

    assert job.usage.calls == 0
    assert job.usage.summary() == "未调用"

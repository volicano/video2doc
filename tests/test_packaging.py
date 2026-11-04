from __future__ import annotations

import tomllib
from pathlib import Path

import video2doc

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return payload["project"]


def test_package_version_matches_pyproject() -> None:
    # README 和 PyPI 元数据容易在发版时漏掉同步，至少先保证代码里的版本一致。
    assert video2doc.__version__ == _project()["version"]


def test_gpu_extra_declares_cuda_runtime() -> None:
    # PyPI 0.1.1 漏发了这个 extra；测试防止后续版本再次只改 README、不改打包元数据。
    extras = _project()["optional-dependencies"]
    assert "gpu" in extras

    gpu = extras["gpu"]
    assert any(item.startswith("nvidia-cublas-cu12") for item in gpu)
    assert any(item.startswith("nvidia-cudnn-cu12") for item in gpu)

"""小文件写入工具：先写同目录临时文件，再原子替换目标文件。

任务状态、阶段产物和成稿都可能被用户或服务进程同时读取。直接 `write_text`
会先截断再写，进程在这之间退出就会留下半截 JSON/HTML。这里统一走
`tempfile` + `os.replace`，保证目标文件要么是旧版本，要么是完整的新版本。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子地把 ``text`` 写到 ``path``；目标父目录不存在时自动创建。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

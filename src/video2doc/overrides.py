"""人工覆盖层：`overrides.json`。

流水线从不写这个文件，它只属于用户。渲染前把覆盖应用到 `stages/write.json`
的文档上，改标题、改正文、换图都不用重新调用模型。

格式（章节号从 1 开始，和 HTML 里显示的一致）：

```json
{
  "title": "教程标题",
  "intro": "导语",
  "sections": {
    "3": {
      "title": "新的章节标题",
      "markdown": "正文，图片用 [[img:1]] 占位",
      "images": ["frames/f17_00218s.jpg"]
    }
  }
}
```

任何字段都可以只写一部分；没写的沿用模型生成的内容。
`images` 是一个有序列表，决定本节 `[[img:N]]` 的编号。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .errors import StageError
from .schema import Document, ImageRef, WrittenSection
from .writing.materials import count_chars
from .writing.write import IMG_RE

OVERRIDES_NAME = "overrides.json"


class SectionOverride(BaseModel):
    title: str | None = None
    markdown: str | None = None
    images: list[str] | None = None


class Overrides(BaseModel):
    schema_version: int = 1
    title: str | None = None
    intro: str | None = None
    sections: dict[int, SectionOverride] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any((self.title, self.intro, self.sections))


def load_overrides(job_dir: Path) -> Overrides | None:
    """读 `overrides.json`。不存在返回 None；格式坏了直接报错，别静默忽略。"""
    path = job_dir / OVERRIDES_NAME
    if not path.exists():
        return None
    try:
        # utf-8-sig：Windows 记事本保存的 JSON 常带 BOM
        return Overrides.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except ValidationError as exc:
        detail = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in detail.get("loc", ())) or "根节点"
        raise StageError(
            f"{OVERRIDES_NAME} 格式不对：{where} {detail.get('msg', '')}".strip(),
            hint=f"参照文档检查 {path}，或删掉它回到模型生成的版本。",
        ) from exc
    except OSError as exc:
        raise StageError(
            f"读不了 {path}：{exc}",
            hint="检查文件是否被其他程序占用。",
        ) from exc


def save_overrides(job_dir: Path, overrides: Overrides) -> Path:
    path = job_dir / OVERRIDES_NAME
    path.write_text(overrides.model_dump_json(indent=2), encoding="utf-8")
    return path


def overrides_digest(job_dir: Path) -> str:
    """覆盖层的指纹。文件内容变了，render 阶段就必须重跑。"""
    path = job_dir / OVERRIDES_NAME
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def apply_overrides(document: Document, overrides: Overrides) -> tuple[Document, list[str]]:
    """把覆盖应用到文档上，返回新文档和需要提示用户的问题。"""
    warnings: list[str] = []
    known = _image_index(document)

    for index in overrides.sections:
        if not any(section.index == index for section in document.sections):
            warnings.append(f"覆盖层里的第 {index} 节不存在，已忽略")

    # 先把每个覆盖的图片列表解析成真实画面：一张图被挪到别的章节后，
    # 原来那节就不该再出现它（否则同一张截图会在全文出现两次）。
    resolved: dict[int, list[ImageRef]] = {}
    for index, change in overrides.sections.items():
        if change.images is None:
            continue
        resolved[index] = _resolve_images(change.images, known, index, warnings)
    claimed = {ref.image for refs in resolved.values() for ref in refs}

    sections: list[WrittenSection] = []
    for section in document.sections:
        change = overrides.sections.get(section.index)
        if change is None:
            if claimed:
                kept = [ref for ref in section.images if ref.image not in claimed]
                if len(kept) != len(section.images):
                    section = section.model_copy(
                        update={
                            "images": [
                                ref.model_copy(update={"id": position})
                                for position, ref in enumerate(kept, 1)
                            ]
                        }
                    )
            sections.append(section)
            continue

        data = section.model_dump()
        if change.title and change.title.strip():
            data["title"] = change.title.strip()
        if change.markdown is not None:
            data["markdown"] = change.markdown
            data["chars"] = count_chars(change.markdown)
        if section.index in resolved:
            data["images"] = [ref.model_dump() for ref in resolved[section.index]]
        sections.append(WrittenSection.model_validate(data))

    title = (overrides.title or "").strip() or document.title
    intro = document.intro if overrides.intro is None else overrides.intro.strip()
    updated = document.model_copy(
        update={
            "title": title,
            "intro": intro,
            "sections": sections,
            "chars": sum(section.chars for section in sections),
            "images": sum(len(section.images) for section in sections),
        }
    )

    for section in updated.sections:
        ids = [int(match) for match in IMG_RE.findall(section.markdown)]
        if ids and max(ids) > len(section.images):
            warnings.append(
                f"第 {section.index} 节的 [[img:{max(ids)}]] 超出图片数量"
                f"（该节只有 {len(section.images)} 张），渲染时会丢掉"
            )
    return updated, warnings


def _image_index(document: Document) -> dict[str, ImageRef]:
    """按完整路径和文件名都能找到原图，方便手写覆盖层。"""
    known: dict[str, ImageRef] = {}
    for section in document.sections:
        for ref in section.images:
            known[ref.image] = ref
            known.setdefault(Path(ref.image).name, ref)
            known.setdefault(ref.image.replace("\\", "/"), ref)
    return known


def _resolve_images(
    images: list[str],
    known: dict[str, ImageRef],
    section_index: int,
    warnings: list[str],
) -> list[ImageRef]:
    refs: list[ImageRef] = []
    for image in images:
        key = image.strip()
        ref = known.get(key) or known.get(Path(key).name)
        if ref is None:
            warnings.append(f"第 {section_index} 节引用了不存在的画面：{image}")
            continue
        refs.append(ref.model_copy(update={"id": len(refs) + 1}))
    return refs

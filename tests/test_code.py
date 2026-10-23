from __future__ import annotations

from video2doc.utils.code import (
    code_block_markdown,
    extract_code_block,
    has_code_frame,
    looks_like_code,
)

NOISY_CODE = """bilibili
oil欧呦
ego-browser|ego（lite)Docs
使用场景解决方案文档企业版日志下载博客
ego-browser nodejs<<'EOF'
口
安装
const task=await useorCreateTaskSpace('search github issues')
核心循环
开始使用
Helper参考
await open0rReuseTab('https://github.com/issues'，{wait:true，
TaskSpace
timeout:20}）
ego(lite)产品介绍
cliLog(awaitsnapshotText())
EOF
键盘与输入
"""

OBSIDIAN_PLUGIN_UI = """Github仓库地址：https:/lgithub.com/liamcain/obsidian-calendar-plugin
当前显示了43个插件：
<Oct2020>
Wed 0ctober28,2020
Simple calendar widget.
Calendar 已安装
作者：Liam Cain
fix:support Windows publishing and cross-platform valida...
ttps://github.com/oil-oil/video-publisher-skil/blob/main/readme.svg
"""

OBSIDIAN_SEARCH_RESULTS = """title:Thread by@karpathy-LLMKnowledgeBases
type:source
raw_file:"Raw/Threadby@karpathy.md"
title:AndrejKarpathyJust10x'dEveryone'sClaudeCode
type:source
raw_file:"Raw/AndrejKarpathy.md"
"""

OBSIDIAN_JOURNAL = """tags:
type：日记
created:{{date))(time))
{date}日记
Templates/日记模板
Thought for 0s
"""

OBSIDIAN_TASKS = """-[/] =>[x],name:'In Progress', type:'IN_PROGRESS'.
-[-]=>[],name:'Cancelled',type:'CANCELLED'.
"""


def test_extract_code_from_noisy_heredoc() -> None:
    code = extract_code_block(NOISY_CODE)

    assert "const task" in code
    assert "await open0rReuseTab" in code
    assert "timeout:20" in code
    assert "核心循环" not in code
    assert "安装" not in code
    assert "EOF" not in code


def test_extract_code_returns_empty_for_plain_ui() -> None:
    assert extract_code_block("bilibili\n首页\n推荐\n设置") == ""


def test_has_code_frame_needs_real_syntax() -> None:
    assert has_code_frame("function outer() { return () => {}; }")
    assert has_code_frame(NOISY_CODE)
    assert not has_code_frame(OBSIDIAN_JOURNAL)
    assert not has_code_frame("ego-browser nodejs<<'EOF'\nconst task\nEOF")
    assert not has_code_frame(OBSIDIAN_TASKS)


def test_extract_code_block_drops_short_fragments() -> None:
    assert extract_code_block("ego-browser nodejs<<'EOF'\nconst task\nEOF") == ""
    assert extract_code_block("const task = await run();\nreturn task;") != ""


def test_looks_like_code_ignores_paren_heavy_ui() -> None:
    assert not looks_like_code("点击这里（设置）然后继续")
    assert looks_like_code("const task = await run();")


def test_looks_like_code_handles_obsidian_ui_and_frontmatter() -> None:
    assert not looks_like_code(OBSIDIAN_PLUGIN_UI)
    assert not looks_like_code(OBSIDIAN_SEARCH_RESULTS)
    assert looks_like_code(OBSIDIAN_JOURNAL)
    assert not looks_like_code(OBSIDIAN_TASKS)


def test_looks_like_code_accepts_real_snippets() -> None:
    assert looks_like_code("const task = await useOrCreateTaskSpace('search');")
    assert looks_like_code("def run():\n    return 1")
    assert looks_like_code("import os\nprint('hi')")
    assert looks_like_code("$ npm install foo\nnpm run build")
    assert looks_like_code("if (ready) {\n  return true;\n}")


def test_looks_like_code_requires_real_syntax_for_prose() -> None:
    assert not looks_like_code("if you want to continue")
    assert not looks_like_code("<div>Hello</div>")


def test_extract_code_block_ignores_prose_frames() -> None:
    assert extract_code_block(OBSIDIAN_JOURNAL) == ""
    assert extract_code_block(OBSIDIAN_TASKS) == ""


def test_code_block_guesses_language() -> None:
    assert code_block_markdown("const task = await run();").startswith("```js")
    assert code_block_markdown("def run():\n    return 1").startswith("```python")

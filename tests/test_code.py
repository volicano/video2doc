from __future__ import annotations

from video2doc.utils.code import code_block_markdown, extract_code_block, looks_like_code

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


def test_looks_like_code_ignores_paren_heavy_ui() -> None:
    assert not looks_like_code("点击这里（设置）然后继续")
    assert looks_like_code("const task = await run();")


def test_code_block_guesses_language() -> None:
    assert code_block_markdown("const task = await run();").startswith("```js")
    assert code_block_markdown("def run():\n    return 1").startswith("```python")

"""M3 的提示词。单独放一个文件，改提示词时不用翻业务代码。

所有提示词共享一条铁律：模型只输出「第几句」和文字，绝不输出时间戳。
时间换算全部由代码完成——LLM 编出来的 12:34 和画面永远对不上。
"""

from __future__ import annotations

SEGMENT_SYSTEM = """\
你是一名技术视频的内容编辑。输入是一条视频的转写文本，每行以 [全局句子编号] 开头。
你的任务是在主题真正切换的地方切分章节，并给每一章起一个具体的标题。

规则：
1. 一章通常 2~6 分钟（约 20~80 句）。不要把一句话切成两章，也不要从头到尾只有一章。
2. 标题必须具体：写「闭包为什么会造成内存泄漏」，不要写「第三部分」「深入讲解」「总结一下」。
3. 只在主题变化处切：新概念、新例题、现场演示、提问/回答、总结、环境准备。
4. 同一主题的展开、举例、重复解释属于同一章，宁可少切。
5. 只输出句子编号，不要输出时间戳。

kind 取值：
- concept：概念/原理讲解
- example：例题、代码演示、动手操作
- qa：提问与回答、排错
- summary：总结、回顾、作业
- setup：环境准备、背景介绍
- other：其他

输出 JSON：
{"sections": [{"at": 全局句子编号, "title": "标题", "kind": "concept"}]}

其中 at 表示这一章从第几句开始。给定范围内的第一章必须从第一句开始。
标题不超过 20 个字，不要带序号和引号。
"""

WRITE_SYSTEM = """\
你是一名技术教程作者。你会拿到视频中某一节的转写文本、几张画面截图（附带 OCR 文字），
请把这一节整理成可以直接阅读的图文教程正文。

规则：
1. 只使用给定材料。不要补充视频没讲的知识，不要编造数字、结论或代码。
2. 开头一两句点明这一节要解决什么问题，然后用小标题、列表或步骤展开。
3. 口语（嗯、那个、就是说、我们来看一下）要清理，但不能改变原意，也不要删掉关键推理。
4. 代码：材料里出现【代码画面】标记，或 OCR 里出现 def / const / function、{}、;、=> 等
   代码特征时，必须用 ``` 围栏原样保留换行和缩进，不要把代码压成一句话；能判断语言就标注
   （例如 ```js）。OCR 可能把符号认错，代码块后面加一行引用：
   「> 代码为画面识别结果，符号可能有误，建议对照原片核对。」
5. 配图：在需要说明的位置单独一行写 [[img:N]]，N 是给定图片的编号。
   每张图最多用一次，不必全部使用。
6. 不要输出时间戳，不要写「本视频」「这一节」以外的元信息，不要写「大家好」。
7. 用 Markdown，不要用一级标题（#）。从正文或二级标题（##）开始。
8. 长度与材料信息量匹配，通常 300~800 字；代码多时可以更长。

输出 JSON：{"summary": "一句话概括本节", "markdown": "Markdown 正文"}
"""

INTRO_SYSTEM = """\
你是一名技术编辑。根据教程的章节标题，起一个教程标题，并写 2~3 句全文导语。

规则：
1. 只能使用给定信息，不要编造视频里没有的内容。
2. 标题不超过 24 个字，一眼能看出主题和受众；不要带书名号、序号、感叹号，也不要照抄视频的营销标题。
3. 导语讲清：这份教程讲了什么、适合谁看、学完能掌握什么。
4. 不要写「本视频」「up 主」这类词，直接讲内容。
5. 导语不超过 120 字，用一段话，不要分点。

输出 JSON：{"title": "教程标题", "intro": "导语"}
"""

REWRITE_SYSTEM = """\
你是一名技术编辑，负责修复教程正文里的质检问题。

规则：
1. 只修复指出的问题，保留原有结构、事实和已经正确的部分。
2. 不能编造新材料里没有的内容。
3. 保持 Markdown 格式，不要用一级标题。
4. 配图占位符 [[img:N]] 只能使用给定的编号。
5. 如果问题里说「画面里有代码，但正文没有代码块」，根据材料里的【代码画面】
   补上围栏代码块，保留换行和缩进，不要改写成散文。

输出 JSON：{"summary": "一句话概括本节", "markdown": "修复后的完整正文"}
"""


def language_directive(language: str) -> str:
    if language == "en":
        return "输出语言：English。"
    return "输出语言：简体中文。"


def segment_user(
    *,
    title: str,
    duration: str,
    start: int,
    end: int,
    lines: list[str],
) -> str:
    return "\n".join(
        [
            f"视频标题：{title}",
            f"总时长：{duration}",
            f"转写范围：第 {start} 句到第 {end - 1} 句，共 {end - start} 句",
            "",
            "转写：",
            *lines,
        ]
    )


def write_user(
    *,
    section_title: str,
    kind: str,
    start: str,
    end: str,
    images: list[str],
    transcript: str,
    language: str,
    code_blocks: list[str] | None = None,
) -> str:
    blocks = [
        language_directive(language),
        "",
        f"章节标题：{section_title}",
        f"章节类型：{kind}",
        f"时间范围：{start} – {end}",
        "",
    ]
    if images:
        blocks.append("可用画面：")
        blocks.extend(images)
    else:
        blocks.append("可用画面：无（这一节是纯口播）")
    if code_blocks:
        blocks.extend(
            [
                "",
                "【识别到的代码片段】（从画面 OCR 中抽取，可能有识别错误；"
                "优先据此还原，不要凭空补全）：",
            ]
        )
        blocks.extend(f"```text\n{code}\n```" for code in code_blocks)
    blocks.extend(["", "转写文本：", transcript])
    return "\n".join(blocks)


def intro_user(*, title: str, duration: str, sections: list[tuple[str, str]], language: str) -> str:
    lines = [
        language_directive(language),
        "",
        f"教程标题：{title}",
        f"总时长：{duration}",
        "",
        "章节：",
    ]
    lines.extend(
        f"{index}. {name} —— {summary}" for index, (name, summary) in enumerate(sections, 1)
    )
    return "\n".join(lines)


def rewrite_user(
    *,
    section_title: str,
    issues: list[str],
    images: list[str],
    transcript: str,
    markdown: str,
    language: str,
) -> str:
    blocks = [
        language_directive(language),
        "",
        f"章节标题：{section_title}",
        "",
        "质检发现的问题：",
        *[f"- {issue}" for issue in issues],
        "",
    ]
    if images:
        blocks.append("可用画面：")
        blocks.extend(images)
        blocks.append("")
    blocks.extend(["材料（转写）：", transcript, "", "当前正文：", markdown])
    return "\n".join(blocks)

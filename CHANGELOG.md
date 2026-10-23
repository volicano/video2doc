# 更新日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 新增

- `v2doc eval`：不调用模型，检查成稿的时间戳、章节覆盖、配图引用、OCR 代码块、质检和 LLM 用量；支持 `--all`、`--json`、`--strict`
- 评测基线：两条真实视频的 OCR 代码画面与正文代码块不一致、图片未引用等问题已经能被自动发现

### 改进

- 代码保真：代码画面 OCR 保留换行和缩进；抽帧时给代码画面保留名额；模型漏写代码块时，从 OCR 的 heredoc 里抽一段代码补上，并保留免责声明
- 写作断点续跑：每写完一节落盘到 `cache/write_sections.json`，LLM 中途失败后重跑不会重写已完成的节；整篇成功收尾后自动清理
- OCR 并行与断点续跑：`ocr.workers` 默认 4 线程（RapidOCR，CPU 上约 1.5x）；每帧结果写入 `cache/ocr_frames.json`，中途失败后不重做已完成帧
- 渲染体积可调：新增 `render.image_width` / `render.image_quality`；`v2doc eval --all` 自动跳过 `_` / `.` 开头的备份目录

### 修正

- LLM JSON 解析改用 `raw_decode`：支持 Markdown 正文里的代码围栏、JSON 后面的尾随文本，以及未转义的 Windows 路径反斜杠

## [0.1.1] - 2026-10-22

打包修正，无代码变更。

### 修正

- PyPI 项目页的安装说明改为 `pip install "video2doc[local,ocr]"` / `pip install video2doc`，不再要求先克隆源码
- README 增加 PyPI badge；使用手册同步 PyPI 安装方式，并更新测试数量

## [0.1.0] - 2026-10-22

首个公开版本：从一条视频链接（或本地文件）到一份单页图文教程的完整流水线。

### 新增

**视频理解流水线（M1–M3）**

- 下载与探测：B 站、YouTube、本地文件；优先复用平台自带字幕
- 语音识别：`faster-whisper`（本地、默认）与 OpenAI 兼容的云端接口
- 可选的术语 / 错别字校正，支持热词表
- 抽帧：场景检测 + 信息密度打分 + 去重
- 画面文字识别：RapidOCR（可选，惰性加载，不装也能跑）
- 镜头 ↔ 口播对齐，生成 `preview.html` 调试视图
- LLM 语义分段、逐节写作、确定性质检与自动返工
- 单文件 HTML（图片内嵌）与 Markdown 双格式导出

**批量处理与人工校对（M4）**

- `v2doc batch`：顺序批量、单项失败隔离、`--skip-done` / `--stop-on-error`
- `v2doc status`：任务进度、失败项、`--json` 输出
- LLM token 用量按任务累计，写入 `job.json`
- `overrides.json` 覆盖层：改标题、导语、单节标题 / 正文 / 图片顺序
- `v2doc render`：不调用模型重新渲染
- `v2doc rewrite`：只重写一节，自动重跑质检和渲染
- `v2doc review`：本地浏览器校对界面（仅 `127.0.0.1`、令牌鉴权、无前端构建），保存即写覆盖层并重新渲染

**工程**

- 11 个阶段统一指纹缓存，支持 `--from` / `--force` / 断点续跑
- 150 个测试、`ruff` 检查、GitHub Actions（Ubuntu / Windows × Python 3.11 / 3.12）
- MIT License，约 1100 行中文[使用手册](docs/使用手册.md)

### 说明

- 多 P 视频只处理第一 P
- OCR 对代码符号的识别不可靠，发布前建议对照原片核对代码
- 语音识别时间戳有约 ±0.5 秒误差
- 纯人脸口播视频只能得到口播文字，没有可用的画面信息
- 写作质量取决于所选模型；30–60 分钟视频通常产生 8–15 次 LLM 调用

完整安装、配置、命令与排错说明见 [docs/使用手册.md](docs/使用手册.md)。

[0.1.0]: https://github.com/volicano/video2doc/releases/tag/v0.1.0
[0.1.1]: https://github.com/volicano/video2doc/releases/tag/v0.1.1

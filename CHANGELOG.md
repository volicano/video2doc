# 更新日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 新增

- 抖音支持：识别 `www.douyin.com/video/<id>`、`v.douyin.com` 短链和完整分享文本；短链自动展开，任务目录使用数字 ID；公开视频无需登录，下载失败时提示 Cookie / 代理
- `video.cookies_file`：支持浏览器导出的 Netscape Cookie 文件，解决新版 Chrome / Edge 应用绑定加密导致 `cookies_from_browser` 解密失败的问题
- `v2doc eval`：不调用模型，检查成稿的时间戳、章节覆盖、配图引用、OCR 代码块、质检和 LLM 用量；支持 `--all`、`--json`、`--strict`
- 评测基线：两条真实视频的 OCR 代码画面与正文代码块不一致、图片未引用等问题已经能被自动发现

### 改进

- 代码保真：代码画面 OCR 保留换行和缩进；抽帧时给代码画面保留名额；清洗出的代码片段会随写作提示一起给模型，模型漏写代码块时再从 OCR 抽一段补上；代码画面判定收紧为行首关键字 / heredoc / 干净的多键 YAML / 多行真实代码，排除 URL、搜索结果重复键和半行 OCR 片段
- 写作断点续跑：每写完一节落盘到 `cache/write_sections.json`，LLM 中途失败后重跑不会重写已完成的节；整篇成功收尾后自动清理
- OCR 并行与断点续跑：`ocr.workers` 默认 4 线程（RapidOCR，CPU 上约 1.5x）；每帧结果写入 `cache/ocr_frames.json`，中途失败后不重做已完成帧
- 渲染体积可调：新增 `render.image_width` / `render.image_quality`；`v2doc eval --all` 自动跳过 `_` / `.` 开头的备份目录
- ASR 可选批处理：新增 `asr.batch_size`（默认 1）；`>1` 时走 faster-whisper 的 batched pipeline，GPU 建议 8，CPU 实测约 1.12x
- GPU ASR：新增可选依赖 `video2doc[gpu]`，自动加载 `site-packages/nvidia/*/bin` 的 CUDA 12 运行库；`device = "auto"` 优先 CUDA，初始化失败自动回退 CPU/int8
- 原生 hotwords：faster-whisper 1.2+ 同时使用 native `hotwords` 和 `initial_prompt`，产品名/缩写更稳；实测 turbo 上 EgoLite/EgoBrowser 不再被听成 eaglelite/eaglebrowser
- `large-v3-turbo` 验证：GPU 上 15.5s / 16.8x，字符相似度 97.1%；仍保留 `large-v3` 默认，turbo 作为速度选项
- 任务身份归一化：不同分享文案、跟踪参数和相对路径会复用同一任务目录；B 站分 P 区分为不同任务；本地同名文件用绝对路径摘要区分。旧的 `job.json` 会自动补上 `target_identity`
- 缓存修正：`skipped` 不再当作永久缓存，补装 OCR / 配置 API Key 后会重新评估；`render.title` 进入写作缓存指纹，只改质检开关不会触发整篇重写
- 更安全的落盘：`job.json`、阶段产物、正文、`overrides.json` 和最终 HTML/Markdown 改为原子写入，进程中断不会留下半截文件
- `chunk_overlap` 校验：超过窗口一半或窗口非正数时直接报配置错误，避免长视频退化成大量重复请求
- CLI 错误处理：损坏的 `job.json`、阶段产物和错误的 `--from` 阶段名不再输出 Rich traceback

### 修正

- LLM JSON 解析改用 `raw_decode`：支持 Markdown 正文里的代码围栏、JSON 后面的尾随文本，以及未转义的 Windows 路径反斜杠
- 抖音 API 403：内置 yt-dlp 上游 PR #17826 的 `x-secsdk-web-signature` 请求签名兼容层；yt-dlp 正式发版包含该修复后会自动跳过

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

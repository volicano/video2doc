# video2doc

把**面试 / 教学视频**转成**带配图的图文教程**。

输入一条 B 站 / YouTube 链接或本地视频，输出一份可以直接发布的学习笔记：结构化的章节、可跳回原片的时间戳、关键画面截图。

> **项目状态：M1（文本层已打通）**
> 当前可用：视频下载 → 音轨抽取 → 字幕复用 / 语音识别 → 可选的术语校正 → 带时间戳的文本。
> 正在做：M2 抽帧 + OCR，M3 LLM 写作 + 单页 HTML。

---

## 为什么不是「把视频丢给大模型」？

因为那样得到的东西流畅但空洞：图和文对不上，时间戳是编的，关键细节全丢。

video2doc 把这件事拆成一条**可缓存、可断点续跑的流水线**，LLM 只在三个地方出手：分段打标、写作、质检。其余全是确定性代码：

```
视频链接 / 本地文件
  ├─ ingest      下载、探测时长、收集字幕          ✅ M1
  ├─ audio       抽 16kHz 单声道音轨               ✅ M1
  ├─ transcript  字幕优先，否则语音识别            ✅ M1
  ├─ correct     术语/错别字校正（可选）           ✅ M1
  ├─ segment     语义分段 + 提问/回答/例题标注      ⏳ M2
  ├─ frames      场景检测 + 信息量打分 + 去重      ⏳ M2
  ├─ ocr         提取画面文字与版面                ⏳ M2
  ├─ align       段落 ↔ 关键帧对齐                 ⏳ M2
  ├─ write       LLM 逐节写作                      ⏳ M3
  ├─ critique    覆盖率 / 一致性检查 → 定向重写    ⏳ M3
  └─ render      单文件 HTML                       ⏳ M3
```

每一步的产物都落在 `<任务目录>/stages/*.json`，指纹一变才重跑。改了提示词不会重新下载视频，改了模型不会重新抽帧。

---

## 安装

需要 **Python 3.11+** 和 **ffmpeg**（`ffmpeg`、`ffprobe` 都要在 PATH 里）。

```bash
# 本地语音识别（离线、免费，推荐）
pip install -e ".[local]"

# 或者只用云端 ASR，不装 torch
pip install -e "."
```

Windows 装 ffmpeg：

```powershell
winget install Gyan.FFmpeg
```

装完先自检：

```bash
v2doc doctor
```

它会逐项告诉你 ffmpeg、yt-dlp、CUDA、API Key、网络连通性哪里有问题，以及怎么修。

---

## 快速开始

```bash
# 1. 生成配置文件（可选，不生成也能跑）
v2doc init

# 2. 处理一条视频
v2doc run "https://www.bilibili.com/video/BVxxxxxxxxx"

# 3. 看转写结果
v2doc preview output/BVxxxxxxxxx

# 4. 导成纯文本，方便自己丢给别的工具
v2doc preview output/BVxxxxxxxxx --export notes.txt
```

重复执行同一条命令会自动续跑；想强制重来加 `--force`，想只重跑某一步加 `--from audio`。

```
output/BVxxxxxxxxx/
├─ job.json              # 任务状态、配置快照、每步指纹
├─ media/                # 原视频、音频、字幕
└─ stages/
   ├─ ingest.json
   ├─ audio.json
   └─ transcript.json    # 带时间戳的句子
```

---

## 配置

`v2doc init` 生成的 `video2doc.toml` 里所有字段都有默认值，只需要写你想改的。

### 本地识别（默认）

```toml
[asr]
provider = "faster-whisper"
model = "large-v3"        # 显存吃紧就换 medium / small
language = "zh"
device = "auto"           # 自动选 cuda，没有就 cpu
compute_type = "auto"     # cuda→float16, cpu→int8
hotwords = ["闭包", "垃圾回收", "时间复杂度", "AQS", "CAS"]
auto_hotwords = true      # 从视频标题和标签自动补充热词
```

`hotwords` 是目前性价比最高的一个开关：中文技术术语（尤其是缩写和英文标识符）本来就是 ASR 的重灾区，把它们塞进 `initial_prompt` 几乎零成本。

### 术语校正（可选）

小模型（`tiny` / `small`）在中文技术内容上常把「闭包」听成「弊包」、「垃圾回收」听成「拉机」。开启校正后，会用 LLM 结合词表只修错别字，**不改写、不改时间戳**：

```toml
[asr]
correct_with_llm = true
glossary = ["闭包", "词法环境", "垃圾回收"]   # 留空则复用 hotwords

[llm]
provider = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
```

> **默认关闭。** 开启意味着字幕文本会被发送到 LLM 服务商，介意隐私就保持关闭。
> 校正只会应用长度变化在 0.5–1.6 倍之间的修改，防止模型自由发挥。

### 云端识别

任何兼容 OpenAI `/audio/transcriptions` 的服务都能用，改三个字段即可：

```toml
[asr]
provider = "openai"
model = "whisper-1"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

> 注意：配图对齐依赖**时间戳**。部分兼容服务只返回纯文本，那样后续 M2 的图文对齐会退化，`v2doc` 会在日志里警告。

### 网络

```toml
[network]
proxy = "http://127.0.0.1:7890"     # YouTube 直连不通时
```

需要登录才能看的视频：

```toml
[video]
cookies_from_browser = "chrome"
```

---

## 设计取舍

**字幕优先于 ASR。** B 站和 YouTube 上大量教学视频自带 CC，质量和速度都优于自己转写，还省一次模型推理。`prefer_subtitles = true` 时，解析不出内容会自动回退到 ASR，不会卡住。

**缓存是围绕「指纹」做的。** 每个阶段的指纹 = hash(阶段名 + 版本 + 参数 + 源文件大小/mtime + 上游指纹)。所以：

- 改 `asr.model` → 只重跑识别；
- 改视频文件 → 全部重跑；
- 什么都没改 → 秒过。

**每个阶段的产物都是可校验的 JSON。** 用 pydantic 建模，上游改字段下游立刻报错，而不是默默算出垃圾。

**provider 用注册表，不写死在代码里。** 加一个新 ASR 服务 = 写一个类 + 一行 `@register`。

---

## 路线图

- [x] **M1** 下载 / 音轨 / 字幕 / 语音识别 / 术语校正
- [ ] **M2** 抽帧（场景检测 + 信息密度打分）、OCR、段落与帧对齐
- [ ] **M3** LLM 分段打标与写作、质检重写、单文件 HTML
- [ ] **M4** 人工校对界面、批量处理
- [ ] **M5** 更多 ASR（通义听悟 / FunASR）、说话人分离、模板系统

---

## 已知限制

- 只处理视频的第一 P（多 P 视频会在日志里提示）。
- 画质低于 720p 时，代码类画面的 OCR 效果会明显下降。
- 语音识别的时间戳有 ±0.5s 误差，M2 抽帧时会留缓冲。
- 没有任何自动发布能力，产出就是文件。

## 合规

本项目仅用于个人学习与内容整理。不存储、不二次分发原始视频；请遵守 B 站 / YouTube 的服务条款与版权规定。用别人的视频做二次创作前，请确认授权。

## License

MIT

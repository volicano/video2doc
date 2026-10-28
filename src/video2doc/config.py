"""配置加载与校验。

设计原则：所有可替换的东西都放进配置，代码里不写死模型名和密钥。
密钥一律通过环境变量注入（只记录变量名，不落盘）。
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import ConfigError

DEFAULT_CONFIG_NAMES = ("video2doc.toml", "v2doc.toml")


class Section(BaseModel):
    """禁止未知字段，让拼写错误在启动时就暴露。"""

    model_config = ConfigDict(extra="forbid")


class VideoConfig(Section):
    # 有 CC 字幕时直接用字幕，跳过 ASR（快、准、省钱）
    prefer_subtitles: bool = True
    # 单条视频的时长上限，防止误传 10 小时直播
    max_duration_min: int = 120
    # 下载时的最大分辨率；太高只增加体积，OCR 收益有限
    max_height: int = 1080
    subtitle_langs: list[str] = Field(
        default_factory=lambda: ["zh-Hans", "zh-CN", "zh", "zh-Hant", "en"]
    )
    # 需要登录才能看的高清/字幕，或抖音要求的浏览器 Cookie；可填 chrome / edge / firefox
    cookies_from_browser: str | None = None
    # 浏览器导出的 Cookie 文件（Netscape 格式）；Chrome/Edge 应用绑定加密导致
    # --cookies-from-browser 解密失败时，优先使用这个
    cookies_file: str | None = None


class ASRConfig(Section):
    # faster-whisper（本地） | openai（任何兼容 /audio/transcriptions 的服务）
    provider: str = "faster-whisper"
    model: str = "large-v3"
    language: str = "zh"
    # 用户手写热词，优先级最高
    hotwords: list[str] = Field(default_factory=list)
    # 从标题/标签自动补充热词
    auto_hotwords: bool = True
    # 额外的 initial_prompt，用于注入领域背景
    initial_prompt: str | None = None
    # 用 LLM 做一遍术语/错别字校正（默认关闭：会把文本发到云端）
    correct_with_llm: bool = False
    # 校正时参考的词表，留空则复用 hotwords
    glossary: list[str] = Field(default_factory=list)

    # faster-whisper 专用
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"  # auto | float16 | int8 | int8_float16
    vad: bool = True
    beam_size: int = 5
    # >1 时启用 faster-whisper 的 batched pipeline；GPU 上建议 8，CPU 上收益有限
    batch_size: int = 1

    # openai 兼容服务专用
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_sec: int = 900


class LLMConfig(Section):
    provider: str = "openai-compatible"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.3
    # 单次回答的最大 token；写作阶段一节正文通常 1~2k token，
    # 但素材长、代码多时会写超，4096 容易把 JSON 截断，留足 8192
    max_tokens: int = 8192
    timeout_sec: int = 300


class OCRConfig(Section):
    provider: str = "auto"  # auto | rapidocr | none
    language: str = "ch"
    # 低于这个置信度的文字块直接丢掉，避免把噪声当内容
    min_confidence: float = 0.5
    # 并行识别的帧数；0 / 1 = 串行。RapidOCR 的 ONNX 推理可并行，CPU 上约 1.5x
    workers: int = 4


class FramesConfig(Section):
    strategy: str = "info-density"  # uniform | info-density
    # 0 = 按视频长度自动决定（短视频 1s，长视频 2~3s）
    sample_interval_sec: float = 0.0
    analysis_width: int = 320
    max_frames: int = 40
    min_gap_sec: float = 8.0
    # 相邻帧差异超过它就算换镜头（0.1 大约相当于画面整体变了 10%）
    change_threshold: float = 0.10
    # 白底 PPT 换页时像素差分很小，感知哈希却能看出来；汉明距离超过它就切
    hash_threshold: int = 12
    # 人脸口播镜头的分数通常低于 0.04，低于阈值直接不选
    min_shot_score: float = 0.04
    # 时间轴分几段做覆盖，避免「前五分钟讲得最密」压掉后面全部内容
    coverage_buckets: int = 8


class WriteConfig(Section):
    # 关掉的话，M2 的文本 / 图片 / 对齐产物照样保留，只是不生成教程
    enabled: bool = True
    # auto | zh | en
    language: str = "auto"
    # 一条视频最多切几节；超出时把相邻最短的两节合并
    max_sections: int = 12
    min_section_sec: float = 60.0
    # 单节超过这个长度就按停顿再切一刀，避免一节讲 20 分钟
    max_section_sec: float = 600.0
    # 分段时每次喂给 LLM 的句子数，长视频会自动分块并带重叠
    chunk_utterances: int = 260
    chunk_overlap: int = 20
    max_images_per_section: int = 4
    # 单节转写超过这个字数就截断，防止个别超长段落撑爆上下文
    max_section_chars: int = 6000
    temperature: float = 0.4
    # 质检发现严重问题时，让 LLM 针对性地重写那一节（额外花费）
    review: bool = False
    max_rewrites: int = 3

    @model_validator(mode="after")
    def _validate_chunks(self) -> WriteConfig:
        if self.chunk_utterances <= 0:
            raise ValueError("write.chunk_utterances 必须大于 0")
        if self.chunk_overlap < 0:
            raise ValueError("write.chunk_overlap 不能为负数")
        # 重叠超过窗口的一半时，步长会小到产生大量重复请求，长视频容易失控。
        if self.chunk_overlap > self.chunk_utterances // 2:
            raise ValueError("write.chunk_overlap 不能超过 write.chunk_utterances 的一半")
        return self


class RenderConfig(Section):
    format: str = "html-single"
    theme: str = "light"  # light | dark
    embed_images: bool = True
    # 内嵌图片的最长边与 WebP 质量；嫌 HTML 大就调小，教程截图默认 1280/78 足够清晰
    image_width: int = 1280
    image_quality: int = 78
    # 生成 preview.html，用来肉眼检查抽帧和 OCR 选得对不对
    debug_html: bool = True
    # 教程标题，留空则用视频标题
    title: str | None = None
    show_toc: bool = True
    # 在图片下方显示 OCR 认出来的画面文字。录屏类视频的 OCR 噪声大，默认关闭
    image_captions: bool = False


class NetworkConfig(Section):
    proxy: str | None = None


class Config(Section):
    video: VideoConfig = Field(default_factory=VideoConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    frames: FramesConfig = Field(default_factory=FramesConfig)
    write: WriteConfig = Field(default_factory=WriteConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)

    @property
    def hash(self) -> str:
        payload = json.dumps(
            self.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def find_config(explicit: Path | None = None, *, cwd: Path | None = None) -> Path | None:
    """按优先级找配置文件：显式指定 > 当前目录的约定文件名。"""
    if explicit is not None:
        if not explicit.exists():
            raise ConfigError(f"配置文件不存在：{explicit}")
        return explicit
    base = cwd or Path.cwd()
    for name in DEFAULT_CONFIG_NAMES:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def load_config(path: Path | None = None, *, cwd: Path | None = None) -> Config:
    resolved = find_config(path, cwd=cwd)
    raw: dict[str, Any] = {}
    if resolved is not None:
        try:
            # Windows 记事本保存的 UTF-8 会带 BOM，tomllib 不认，先剥掉
            text = resolved.read_bytes().removeprefix(b"\xef\xbb\xbf").decode("utf-8")
            raw = tomllib.loads(text)
        except UnicodeDecodeError as exc:
            raise ConfigError(f"配置文件不是 UTF-8 编码：{resolved}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置文件语法错误：{resolved}\n{exc}") from exc
        except OSError as exc:
            raise ConfigError(f"无法读取配置文件：{resolved}（{exc}）") from exc

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"配置文件校验失败：{resolved or '<默认值>'}\n{_format_errors(exc)}"
        ) from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def config_snapshot(config: Config) -> dict[str, Any]:
    """写入 job 目录，保证半年后还能复现这次运行。"""
    return config.model_dump()


EXAMPLE_CONFIG = """\
# video2doc 配置示例：复制为 video2doc.toml 后按需修改
# 所有字段都有默认值，只有想改的才需要写在这里

[video]
prefer_subtitles = true      # 有 CC 字幕就直接用，跳过语音识别
max_height = 1080
# cookies_from_browser = "chrome"   # 需要登录才能看的高清/字幕
# cookies_file = "C:/path/to/cookies.txt"  # 浏览器导出的 Netscape Cookie 文件

[asr]
# 本地：faster-whisper（需 pip install "video2doc[local]"）
# 云端：openai（任何兼容 OpenAI /audio/transcriptions 的服务）
provider = "faster-whisper"
model = "large-v3"
language = "zh"
hotwords = ["闭包", "垃圾回收", "时间复杂度", "AQS", "CAS"]
auto_hotwords = true
device = "auto"
compute_type = "auto"
# 用 LLM 修术语/错别字（默认关闭：会把字幕文本发到云端）
correct_with_llm = false
# glossary = ["闭包", "词法环境", "垃圾回收"]

# 用云端时改成这样：
# provider   = "openai"
# model      = "whisper-1"
# base_url   = "https://api.openai.com/v1"
# api_key_env = "OPENAI_API_KEY"

[llm]
# 一个适配器覆盖 DeepSeek / 通义 / GLM / Ollama / vLLM
provider = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
# max_tokens = 8192            # 单节正文太长被截断时调大

[ocr]
# 画面文字识别：rapidocr-onnxruntime（离线）；没装会自动跳过
provider = "auto"            # auto | rapidocr | none
min_confidence = 0.5

[frames]
strategy = "info-density"    # uniform 用于对比
max_frames = 40
min_gap_sec = 8
change_threshold = 0.10      # 相邻帧差异超过它就算换镜头
hash_threshold = 12          # 白底 PPT 换页的感知哈希阈值
min_shot_score = 0.04        # 低于它基本是纯人脸口播
coverage_buckets = 8         # 时间轴分几段，保证覆盖

[write]
# 用 LLM 把转写整理成图文教程；关掉则只保留文本 / 图片 / 对齐数据
enabled = true
language = "auto"            # auto | zh | en
max_sections = 12            # 超出时合并相邻最短的两节
min_section_sec = 60
max_section_sec = 600        # 单节太长会按停顿再切一刀
max_images_per_section = 4
temperature = 0.4
# 质检发现严重问题时，让模型针对那一节重写（额外花费）
review = false
max_rewrites = 3

[render]
format = "html-single"
theme = "light"
embed_images = true          # 图片 base64 内嵌，产出单个可分享的 HTML
debug_html = true            # 生成 preview.html，方便验收抽帧
show_toc = true
image_captions = false       # 图片下方显示 OCR 画面文字（录屏类视频噪声大，默认关）
# title = "自定义教程标题"

[network]
# proxy = "http://127.0.0.1:7890"   # YouTube / 抖音直连不通时使用
"""

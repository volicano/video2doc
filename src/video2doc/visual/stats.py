"""画面指标：全部用 numpy + Pillow，不依赖 OpenCV。

为什么不用 cv2：一个 Haar 级联换 60MB 的依赖不划算。教学视频里需要区分的是
「PPT / 代码 / 白板」和「人脸口播」，用边缘密度 + 肤色占比就够了。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# 归一化参数，按 320px 宽的分析帧调的
_EDGE_GAIN = 5.0
_EDGE_FLOOR = 28.0
_SKIN_PIVOT = 0.30


@dataclass
class FrameStat:
    edge: float
    skin: float
    brightness: float
    diff: float = 0.0


def load_gray(path: Path, *, width: int | None = None) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L")
        if width and gray.width > width:
            height = max(1, round(gray.height * width / gray.width))
            gray = gray.resize((width, height), Image.BILINEAR)
        return np.asarray(gray, dtype=np.float32)


def load_rgb(path: Path, *, width: int | None = None) -> np.ndarray:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        if width and rgb.width > width:
            height = max(1, round(rgb.height * width / rgb.width))
            rgb = rgb.resize((width, height), Image.BILINEAR)
        return np.asarray(rgb, dtype=np.float32)


def edge_score(gray: np.ndarray) -> float:
    """强边缘像素占比。

    文字和代码画面有大量高对比笔画，人脸则是低频渐变，
    所以这个指标天然能把「信息画面」和「口播画面」分开。
    """
    gy, gx = np.gradient(gray)
    magnitude = np.hypot(gx, gy)
    ratio = float((magnitude > _EDGE_FLOOR).mean())
    return float(min(1.0, ratio * _EDGE_GAIN))


def skin_score(rgb: np.ndarray) -> float:
    """YCbCr 肤色阈值，用于识别「人脸特写」。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    mask = (cb >= 77.0) & (cb <= 127.0) & (cr >= 133.0) & (cr <= 173.0) & (y > 40.0)
    return float(mask.mean())


def brightness(gray: np.ndarray) -> float:
    return float(gray.mean() / 255.0)


def frame_diff(current: np.ndarray, previous: np.ndarray) -> float:
    if current.shape != previous.shape:
        return 1.0
    return float(np.abs(current - previous).mean() / 255.0)


def sharpness(gray: np.ndarray) -> float:
    """拉普拉斯方差，越低越糊。用于剔除转场/运动模糊帧。"""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    center = gray[1:-1, 1:-1]
    laplacian = -4.0 * center + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    return float(laplacian.var())


def analyze(path: Path, previous_gray: np.ndarray | None = None) -> tuple[FrameStat, np.ndarray]:
    gray = load_gray(path, width=320)
    rgb = load_rgb(path, width=320)
    stat = FrameStat(
        edge=edge_score(gray),
        skin=skin_score(rgb),
        brightness=brightness(gray),
        diff=frame_diff(gray, previous_gray) if previous_gray is not None else 1.0,
    )
    return stat, gray


def classify(edge: float, skin: float, bright: float) -> str:
    """粗分类，只用于选题和排障；最终类型还会用 OCR 结果细分。"""
    if skin >= 0.18 and edge < 0.12:
        return "face"
    if bright >= 0.72 and edge < 0.16:
        return "whiteboard"
    if edge >= 0.16:
        return "slide"
    return "screen"


def info_score(edge: float, skin: float) -> float:
    """信息密度：边缘多、人脸少。"""
    penalty = min(1.0, skin / _SKIN_PIVOT)
    return float(max(0.0, edge * (1.0 - penalty)))


def dhash(path: Path) -> int:
    """64 位差值哈希，用来去掉内容几乎重复的帧。"""
    return dhash_gray(load_gray(path))


def dhash_gray(gray: np.ndarray) -> int:
    """已经读过灰度图时，别再解一次 JPEG。"""
    small = Image.fromarray(gray.astype("uint8")).resize((9, 8), Image.BILINEAR)
    pixels = np.asarray(small, dtype=np.int16)
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")

"""
图片预处理工具（无需 OCR / Tesseract）。

功能：
  - 等比缩放：长边 <= MAX_LONG_EDGE（默认 1500px，只缩不放大）
  - 转 RGB：去 alpha 通道（RGBA/LA/P → 白底 RGB）
  - JPEG 压缩（quality 70-85，可配置）
  - 缓存到 data/images_cache/

配置项（可在调用时覆盖）：
  MAX_LONG_EDGE = 1500
  JPEG_QUALITY  = 80
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from PIL import Image

# ── 默认配置 ────────────────────────────────────────────────────
MAX_LONG_EDGE: int = 1500   # 长边上限（像素）
JPEG_QUALITY: int = 80      # JPEG 质量（70-85 均可）

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "images_cache"


def preprocess_image(
    uploaded_file,
    max_long_edge: int = MAX_LONG_EDGE,
    jpeg_quality: int = JPEG_QUALITY,
) -> dict:
    """
    预处理上传图片，返回字典：

    {
      "path"             : Path,          # 缓存文件路径
      "original_size"    : (w, h),        # 原始像素尺寸
      "optimized_size"   : (w, h),        # 优化后尺寸
      "bytes"            : bytes,         # 压缩后 JPEG（用于 Vision API）
      "size_kb_original" : int,           # 原始大小（KB）
      "size_kb_optimized": int,           # 优化后大小（KB）
    }

    无需 OCR；Pillow 是唯一依赖。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 读取原始字节（同时计算原始大小）
    raw_bytes = uploaded_file.read()
    size_kb_original = max(1, len(raw_bytes) // 1024)

    img = Image.open(io.BytesIO(raw_bytes))
    original_size: tuple[int, int] = img.size

    # ── 去 alpha / 统一转 RGB ──────────────────────────────────
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # ── 等比缩放（只缩不放大）─────────────────────────────────
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    optimized_size: tuple[int, int] = img.size

    # ── 压缩为 JPEG 并缓存 ────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    src_name = getattr(uploaded_file, "name", "image")
    stem = Path(src_name).stem[:40]           # 避免路径过长
    cache_path = CACHE_DIR / f"{ts}_{stem}.jpg"

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    img_bytes = buf.getvalue()
    size_kb_optimized = max(1, len(img_bytes) // 1024)

    cache_path.write_bytes(img_bytes)

    return {
        "path": cache_path,
        "original_size": original_size,
        "optimized_size": optimized_size,
        "bytes": img_bytes,
        "size_kb_original": size_kb_original,
        "size_kb_optimized": size_kb_optimized,
    }

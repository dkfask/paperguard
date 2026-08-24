"""应用层上传校验与资源预算。"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

DOCX_MAX_BYTES = 50 * 1024 * 1024
IMPORT_MAX_BYTES = 50 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
IMAGE_MAX_PIXELS = 40_000_000
ZIP_MAX_ENTRIES = 10_000
ZIP_MAX_UNCOMPRESSED = 250 * 1024 * 1024
ZIP_MAX_RATIO = 100.0


def read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件过大, 上限为 {max_bytes // (1024 * 1024)}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_docx_bytes(data: bytes) -> None:
    if not data.startswith(b"PK"):
        raise HTTPException(status_code=422, detail="文件不是有效的 DOCX 压缩包")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > ZIP_MAX_ENTRIES:
                raise HTTPException(status_code=413, detail="DOCX 内部文件数量超限")
            total = 0
            for info in infos:
                total += info.file_size
                if total > ZIP_MAX_UNCOMPRESSED:
                    raise HTTPException(status_code=413, detail="DOCX 解压后体积超限")
                if info.compress_size and info.file_size / info.compress_size > ZIP_MAX_RATIO:
                    raise HTTPException(status_code=422, detail="DOCX 压缩比异常, 已拒绝处理")
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise HTTPException(status_code=422, detail="DOCX 缺少必要的 OOXML 部件")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="DOCX 压缩包损坏") from exc


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return struct.unpack("<HH", data[6:10])


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0xD8, 0xD9):
            continue
        if pos + 2 > len(data):
            return None
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        if length < 2 or pos + length > len(data):
            return None
        if marker in range(0xC0, 0xC4) or marker in range(0xC5, 0xC8) or marker in range(0xC9, 0xCC) or marker in range(0xCD, 0xD0):
            if length < 7:
                return None
            height, width = struct.unpack(">HH", data[pos + 3:pos + 7])
            return width, height
        pos += length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    if data[12:16] == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    return None


def validate_image(data: bytes, filename: str | None = None) -> str:
    if len(data) > IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="图片过大, 上限为 10MB")
    candidates = (
        ("png", _png_size(data)),
        ("jpeg", _jpeg_size(data)),
        ("gif", _gif_size(data)),
        ("webp", _webp_size(data)),
    )
    kind, size = next(((kind, size) for kind, size in candidates if size), (None, None))
    if not kind or not size:
        raise HTTPException(status_code=400, detail="仅支持真实的 PNG/JPEG/GIF/WebP 图片")
    width, height = size
    if width <= 0 or height <= 0 or width * height > IMAGE_MAX_PIXELS:
        raise HTTPException(status_code=413, detail="图片尺寸或像素总数超限")
    return kind


def safe_docx_filename(filename: str) -> str:
    name = Path(filename or "fixed.docx").name
    if name != filename or any(ch in name for ch in "\r\n\\/"):
        raise HTTPException(status_code=400, detail="下载文件名不合法")
    if not name.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="下载文件名必须以 .docx 结尾")
    return name

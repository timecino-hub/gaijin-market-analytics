from __future__ import annotations

import os
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from api.screen_recognition.contracts import ImageInfo


ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
BLOCKED_ZIP_SUFFIXES = {".exe", ".bat", ".cmd", ".ps1", ".sh", ".msi", ".dll", ".scr"}


class ImageReadError(ValueError):
    pass


class UnsafeArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ZipExtractResult:
    output_dir: Path
    image_count: int
    filenames: tuple[str, ...]


def ensure_safe_filename(filename: str) -> None:
    path = Path(filename)
    if path.is_absolute() or len(path.parts) != 1 or filename in {"", ".", ".."}:
        raise ValueError("filename must be a simple basename.")
    if any(part == ".." for part in path.parts):
        raise ValueError("filename must not contain path traversal.")


def is_allowed_image_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMAGE_SUFFIXES


def list_image_files(images_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in images_dir.iterdir()
        if path.is_file() and is_allowed_image_filename(path.name)
    )


def read_image_info(path: Path) -> ImageInfo:
    if not path.is_file() or os.path.islink(path):
        raise ImageReadError("Image is missing, not a regular file, or is a symlink.")
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ImageReadError("Unsupported image extension.")
    header = path.read_bytes()[:64 * 1024]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = _read_png_dimensions(header)
        return ImageInfo(filename=path.name, width=width, height=height, format="png")
    if header.startswith(b"\xff\xd8"):
        width, height = _read_jpeg_dimensions(header)
        return ImageInfo(filename=path.name, width=width, height=height, format="jpeg")
    raise ImageReadError("File content is not PNG or JPEG.")


def _read_png_dimensions(header: bytes) -> tuple[int, int]:
    if len(header) < 24 or header[12:16] != b"IHDR":
        raise ImageReadError("Invalid PNG header.")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ImageReadError("Invalid PNG dimensions.")
    return width, height


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            if width <= 0 or height <= 0:
                raise ImageReadError("Invalid JPEG dimensions.")
            return width, height
        index += segment_length
    raise ImageReadError("Invalid or unsupported JPEG header.")


def safe_extract_images_zip(zip_path: Path, output_dir: Path) -> ZipExtractResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    filenames: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            parts = Path(name).parts
            if any(part in {"", ".", ".."} for part in parts):
                raise UnsafeArchiveError("ZIP contains an unsafe path.")
            basename = Path(name).name
            if basename != name:
                raise UnsafeArchiveError("ZIP must contain images at the archive root.")
            suffix = Path(basename).suffix.lower()
            if suffix in BLOCKED_ZIP_SUFFIXES:
                raise UnsafeArchiveError("ZIP contains an executable or script file.")
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise UnsafeArchiveError("ZIP contains a non-image file.")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise UnsafeArchiveError("ZIP contains a symbolic link.")
            target = output_dir / basename
            resolved_target = target.resolve()
            resolved_output = output_dir.resolve()
            if resolved_output not in resolved_target.parents and resolved_target != resolved_output:
                raise UnsafeArchiveError("ZIP extraction would leave the output directory.")
            with archive.open(info) as source, target.open("wb") as destination:
                destination.write(source.read())
            read_image_info(target)
            filenames.append(basename)
    return ZipExtractResult(output_dir=output_dir, image_count=len(filenames), filenames=tuple(filenames))

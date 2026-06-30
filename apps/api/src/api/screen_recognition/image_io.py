from __future__ import annotations

import os
import stat
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from api.screen_recognition.contracts import ImageInfo


ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
BLOCKED_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
BLOCKED_ZIP_SUFFIXES = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".deb",
    ".dll",
    ".exe",
    ".jar",
    ".js",
    ".lnk",
    ".msi",
    ".ps1",
    ".rpm",
    ".scr",
    ".sh",
    ".vbs",
}
MAX_ZIP_COMPRESSION_RATIO = 100
MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


class ImageReadError(ValueError):
    pass


class UnsafeArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ZipExtractResult:
    output_dir: Path
    image_count: int
    filenames: tuple[str, ...]


@dataclass(frozen=True)
class SafeZipMember:
    archive_name: str
    output_name: str
    file_size: int
    compress_size: int


@dataclass(frozen=True)
class SafeZipScanResult:
    layout: str
    wrapper_dir: str | None
    members: tuple[SafeZipMember, ...]
    total_uncompressed_size: int
    total_compressed_size: int


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
    scan = scan_safe_images_zip(zip_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise UnsafeArchiveError("ZIP extraction output directory must be empty.")
    filenames: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in scan.members:
            target = output_dir / member.output_name
            resolved_target = target.resolve()
            resolved_output = output_dir.resolve()
            if resolved_output not in resolved_target.parents and resolved_target != resolved_output:
                raise UnsafeArchiveError("ZIP extraction would leave the output directory.")
            with archive.open(member.archive_name) as source:
                payload = source.read()
            if len(payload) != member.file_size:
                raise UnsafeArchiveError("ZIP member size changed during extraction.")
            with target.open("xb") as destination:
                destination.write(payload)
            if target.read_bytes() != payload:
                raise UnsafeArchiveError("ZIP extracted bytes did not match archive member bytes.")
            read_image_info(target)
            filenames.append(member.output_name)
    return ZipExtractResult(output_dir=output_dir, image_count=len(filenames), filenames=tuple(filenames))


def scan_safe_images_zip(zip_path: Path) -> SafeZipScanResult:
    file_entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    directory_entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    total_uncompressed_size = 0
    total_compressed_size = 0
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            parts = _safe_zip_parts(info)
            total_uncompressed_size += info.file_size
            total_compressed_size += info.compress_size
            _validate_zip_member_type(info)
            _validate_zip_member_size(info)
            if _is_zip_directory(info):
                directory_entries.append((info, parts))
                continue
            suffix = Path(parts[-1]).suffix.lower()
            if suffix in BLOCKED_ARCHIVE_SUFFIXES:
                raise UnsafeArchiveError("ZIP contains a nested archive.")
            if suffix in BLOCKED_ZIP_SUFFIXES:
                raise UnsafeArchiveError("ZIP contains an executable, script, or shortcut file.")
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise UnsafeArchiveError("ZIP contains a non-image file.")
            file_entries.append((info, parts))
    if not file_entries:
        raise UnsafeArchiveError("ZIP contains no images.")
    if total_uncompressed_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise UnsafeArchiveError("ZIP uncompressed size is too large.")

    root_files = [(info, parts) for info, parts in file_entries if len(parts) == 1]
    wrapped_files = [(info, parts) for info, parts in file_entries if len(parts) == 2]
    nested_files = [(info, parts) for info, parts in file_entries if len(parts) > 2]
    if nested_files:
        raise UnsafeArchiveError("ZIP contains nested directories deeper than one wrapper.")
    if root_files and wrapped_files:
        raise UnsafeArchiveError("ZIP mixes root-level images and wrapped images.")

    if root_files:
        _reject_directory_entries(directory_entries)
        layout = "root"
        wrapper_dir = None
        selected = [(info, parts[-1]) for info, parts in root_files]
    else:
        wrapper_names = {parts[0] for _info, parts in wrapped_files}
        if len(wrapper_names) != 1:
            raise UnsafeArchiveError("ZIP must contain exactly one top-level wrapper directory.")
        wrapper_dir = next(iter(wrapper_names))
        _validate_wrapper_directory_entries(directory_entries, wrapper_dir)
        layout = "single_top_level_directory"
        selected = [(info, parts[-1]) for info, parts in wrapped_files]

    output_names = [output_name for _info, output_name in selected]
    if len({name.lower() for name in output_names}) != len(output_names):
        raise UnsafeArchiveError("ZIP contains duplicate image filenames.")

    return SafeZipScanResult(
        layout=layout,
        wrapper_dir=wrapper_dir,
        members=tuple(
            SafeZipMember(
                archive_name=info.filename,
                output_name=output_name,
                file_size=info.file_size,
                compress_size=info.compress_size,
            )
            for info, output_name in selected
        ),
        total_uncompressed_size=total_uncompressed_size,
        total_compressed_size=total_compressed_size,
    )


def _safe_zip_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    raw_name = info.filename
    if "\x00" in raw_name:
        raise UnsafeArchiveError("ZIP contains an unsafe path.")
    windows_path = PureWindowsPath(raw_name)
    if raw_name.startswith(("/", "\\")) or windows_path.drive or windows_path.root.startswith("\\\\"):
        raise UnsafeArchiveError("ZIP contains an absolute path.")
    parts = tuple(part for part in raw_name.replace("\\", "/").split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise UnsafeArchiveError("ZIP contains an unsafe path.")
    return parts


def _is_zip_directory(info: zipfile.ZipInfo) -> bool:
    return info.is_dir() or info.filename.endswith(("/", "\\"))


def _validate_zip_member_type(info: zipfile.ZipInfo) -> None:
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise UnsafeArchiveError("ZIP contains a symbolic link.")
    if _is_zip_directory(info):
        if file_type not in {0, stat.S_IFDIR}:
            raise UnsafeArchiveError("ZIP contains a special file.")
        return
    if file_type not in {0, stat.S_IFREG}:
        raise UnsafeArchiveError("ZIP contains a hard link or special file.")


def _validate_zip_member_size(info: zipfile.ZipInfo) -> None:
    if info.compress_size == 0 and info.file_size > 0:
        raise UnsafeArchiveError("ZIP has an abnormal compression ratio.")
    if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
        raise UnsafeArchiveError("ZIP has an abnormal compression ratio.")


def _reject_directory_entries(directory_entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]]) -> None:
    if directory_entries:
        raise UnsafeArchiveError("ZIP root layout must not contain directory entries.")


def _validate_wrapper_directory_entries(
    directory_entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]], wrapper_dir: str
) -> None:
    for _info, parts in directory_entries:
        if parts != (wrapper_dir,):
            raise UnsafeArchiveError("ZIP contains an unexpected directory entry.")

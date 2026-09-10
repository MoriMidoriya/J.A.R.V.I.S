"""Path policy helpers shared by file/app tools."""

from __future__ import annotations

import os
from pathlib import Path

from jarvis.config import get_settings

KNOWN_FOLDERS = {
    "desktop": "Desktop",
    "documents": "Documents",
    "docs": "Documents",
    "downloads": "Downloads",
    "download": "Downloads",
    "pictures": "Pictures",
    "photos": "Pictures",
    "videos": "Videos",
    "movies": "Videos",
    "music": "Music",
    "home": "",
    "user": "",
    "profile": "",
}

VIDEO_EXT = {"mp4", "mkv", "avi", "mov", "wmv", "webm", "m4v", "flv", "ts", "mpg", "mpeg"}
AUDIO_EXT = {"mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "tiff", "svg"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "epub", "odt", "rtf"}
CODE_EXT = {"py", "js", "ts", "tsx", "jsx", "html", "css", "json", "yaml", "yml", "toml", "c", "cpp", "h", "java", "go", "rs", "cs", "sh", "ps1", "sql", "ipynb"}
ARCHIVE_EXT = {"zip", "rar", "7z", "tar", "gz", "iso"}
EXE_EXT = {"exe", "msi", "lnk", "bat", "cmd"}

KIND_EXT = {
    "video": VIDEO_EXT,
    "movie": VIDEO_EXT,
    "audio": AUDIO_EXT,
    "music": AUDIO_EXT,
    "image": IMAGE_EXT,
    "photo": IMAGE_EXT,
    "document": DOC_EXT,
    "pdf": {"pdf"},
    "code": CODE_EXT,
    "archive": ARCHIVE_EXT,
    "app": EXE_EXT,
}


def home() -> Path:
    return Path(os.path.expandvars("%USERPROFILE%"))


def known_folder(name: str) -> Path | None:
    key = name.strip().lower().rstrip("s") if name.strip().lower() not in KNOWN_FOLDERS else name.strip().lower()
    sub = KNOWN_FOLDERS.get(name.strip().lower(), KNOWN_FOLDERS.get(key))
    if sub is None:
        return None
    return home() / sub if sub else home()


def is_allowed(path: str | Path) -> bool:
    p = Path(path).resolve()
    perms = get_settings().permissions
    for d in perms.expanded_denied():
        try:
            p.relative_to(d.resolve())
            return False
        except (ValueError, OSError):
            continue
    roots = perms.expanded_roots()
    if not roots:
        return True
    for r in roots:
        try:
            p.relative_to(r.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def human_size(n: int | None) -> str:
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

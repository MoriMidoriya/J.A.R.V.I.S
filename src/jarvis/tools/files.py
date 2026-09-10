"""Find and open files, folders and media on this PC."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import Field
from rapidfuzz import fuzz

from jarvis.tools import everything
from jarvis.tools._paths import AUDIO_EXT, KIND_EXT, VIDEO_EXT, human_size, is_allowed, known_folder
from jarvis.tools.registry import ToolError, tool

JUNK_PARTS = {"node_modules", ".git", "__pycache__", "$recycle.bin", ".venv", "venv", "site-packages", "appdata", ".cache"}
Kind = Literal["any", "file", "folder", "video", "audio", "image", "document", "pdf", "code", "archive"]
When = Literal["any", "today", "yesterday", "thisweek", "lastweek", "thismonth", "lastmonth", "thisyear"]
Sort = Literal["recent", "name", "largest"]


def _is_junk(p: str) -> bool:
    parts = {s.lower() for s in Path(p).parts}
    return bool(parts & JUNK_PARTS)


def _hit_dict(path: str, is_folder: bool, size: int | None, modified: Any) -> dict[str, Any]:
    return {
        "path": path,
        "name": Path(path).name,
        "kind": "folder" if is_folder else (Path(path).suffix.lstrip(".").lower() or "file"),
        "size": human_size(size) if not is_folder else "",
        "modified": modified.strftime("%Y-%m-%d %H:%M") if modified else "",
    }


def _everything_query(query: str, kind: Kind, folder: str | None, when: When) -> str:
    terms: list[str] = []
    if folder:
        f = folder.rstrip("\\") + "\\"
        terms.append(f'"{f}"')
    if kind == "folder":
        terms.append("folder:")
    elif kind == "file":
        terms.append("file:")
    elif kind != "any" and kind in KIND_EXT:
        terms.append("ext:" + ";".join(sorted(KIND_EXT[kind])))
    if when != "any":
        terms.append(f"dm:{when}")
    terms.append(query.strip())
    return " ".join(t for t in terms if t)


def _windows_search(query: str, kind: Kind, folder: str | None, max_results: int) -> list[dict[str, Any]]:
    """Fallback: Windows Search index via ADO/OLE DB (slower, only indexed locations)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        words = [w.replace("'", "''") for w in query.split() if w]
        conds = [f"System.FileName LIKE '%{w}%'" for w in words]
        if kind == "folder":
            conds.append("System.ItemType = 'Directory'")
        elif kind == "file":
            conds.append("System.ItemType <> 'Directory'")
        elif kind != "any" and kind in KIND_EXT:
            conds.append("(" + " OR ".join(f"System.ItemType = '.{e}'" for e in sorted(KIND_EXT[kind])) + ")")
        if folder:
            conds.append(f"SCOPE='file:{folder}'")
        if not conds:
            return []
        where = " AND ".join(conds)
        sql = f"SELECT TOP {max_results} System.ItemPathDisplay, System.Size, System.DateModified, System.ItemType FROM SYSTEMINDEX WHERE {where} ORDER BY System.DateModified DESC"
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(sql, conn)
        out = []
        while not rs.EOF:
            path = rs.Fields.Item("System.ItemPathDisplay").Value
            size = rs.Fields.Item("System.Size").Value
            dm = rs.Fields.Item("System.DateModified").Value
            is_dir = rs.Fields.Item("System.ItemType").Value == "Directory"
            out.append(_hit_dict(path, is_dir, int(size) if size is not None else None, dm))
            rs.MoveNext()
        rs.Close()
        conn.Close()
        return out
    finally:
        pythoncom.CoUninitialize()


STOPWORDS = {
    "the", "a", "an", "my", "of", "for", "in", "on", "to", "and", "that", "this", "about", "from", "with",
    "file", "files", "folder", "folders", "document", "documents", "doc", "docs", "video", "videos", "movie",
    "movies", "song", "songs", "photo", "photos", "picture", "pictures", "image", "images", "pdf", "project",
    "called", "named", "titled", "some", "any", "recent", "latest", "new", "old",
}


def _query_variants(query: str) -> list[str]:
    """Progressively looser search strings: as-is → without filler words → any-word (OR)."""
    words = [w for w in re.split(r"\s+", query.strip()) if w]
    out = [" ".join(words)] if words else []
    core = [w for w in words if w.lower() not in STOPWORDS]
    if core and core != words:
        out.append(" ".join(core))
    if len(core) > 1:
        out.append("<" + "|".join(core) + ">")
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


def _run_backends(q: str, kind: Kind, folder_path: str | None, when: When, sort: Sort) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if everything.available():
        eq = _everything_query(q, kind, folder_path, when)
        logger.debug("everything query: {}", eq)
        try:
            hits = everything.search(eq, max_results=300, sort=sort, match_path=bool(folder_path))
            results = [_hit_dict(h.path, h.is_folder, h.size, h.modified) for h in hits]
        except Exception as e:  # noqa: BLE001
            logger.warning("Everything failed ({}); falling back to Windows Search", e)
    if not results and "<" not in q:  # Windows Search fallback has no OR syntax here
        try:
            results = _windows_search(q, kind, folder_path, 300)
        except Exception as e:  # noqa: BLE001
            logger.warning("Windows Search failed: {}", e)
    return [r for r in results if is_allowed(r["path"]) and not _is_junk(r["path"])]


def search_paths(query: str, kind: Kind = "any", folder: str | None = None, when: When = "any", sort: Sort = "recent", limit: int = 8) -> list[dict[str, Any]]:
    folder_path: str | None = None
    if folder:
        kf = known_folder(folder)
        folder_path = str(kf) if kf else (folder if os.path.isdir(folder) else None)

    results: list[dict[str, Any]] = []
    for variant in _query_variants(query) or [""]:
        if not variant and not folder_path:
            break
        results = _run_backends(variant, kind, folder_path, when, sort)
        if results:
            break
    # Light re-rank: prefer names that fuzzy-match the whole query.
    if sort == "recent" and results:
        results.sort(key=lambda r: -fuzz.WRatio(query.lower(), r["name"].lower()))
    return results[:limit]


@tool(summary="Search: {query}")
def search_files(
    query: Annotated[str, Field(description="Words from the file/folder name, e.g. 'interstellar', 'tax pdf 2025'. Keep it short; omit words like 'file' or 'folder'.")],
    kind: Annotated[Kind, Field(description="Filter by type")] = "any",
    folder: Annotated[str | None, Field(description="Limit to a folder: 'downloads', 'desktop', 'documents', 'videos', or an absolute path")] = None,
    when: Annotated[When, Field(description="Modified-time filter")] = "any",
    sort: Annotated[Sort, Field(description="recent = newest first (default), largest = biggest first")] = "recent",
) -> list[dict[str, Any]]:
    """Search the whole PC for files and folders by name (instant, indexed). Returns up to 8 matches with full paths; then call open_path with the chosen path."""
    if not query.strip() and not folder:
        raise ToolError("Give me at least a word to search for.")
    res = search_paths(query, kind, folder, when, sort)
    if not res:
        raise ToolError(f"Nothing found for '{query}'" + (f" in {folder}" if folder else "") + ".")
    return res


@tool(summary="Open {path}")
def open_path(path: Annotated[str, Field(description="Absolute path of a file or folder, usually from search_files")]) -> str:
    """Open a file with its default application, or a folder in Explorer."""
    p = Path(os.path.expandvars(path))
    if not p.exists():
        raise ToolError(f"Path does not exist: {path}")
    if not is_allowed(p):
        raise ToolError("That location is outside the folders I'm permitted to open.")
    os.startfile(str(p))  # noqa: S606
    return f"Opened {'folder' if p.is_dir() else 'file'} {p.name}."


@tool(summary="Reveal {path}")
def reveal_in_explorer(path: Annotated[str, Field(description="Absolute path of a file to show selected in Explorer")]) -> str:
    """Show a file selected in its containing folder in Explorer (without opening it)."""
    p = Path(os.path.expandvars(path))
    if not p.exists():
        raise ToolError(f"Path does not exist: {path}")
    if not is_allowed(p):
        raise ToolError("That location is outside the folders I'm permitted to open.")
    subprocess.Popen(["explorer.exe", "/select,", str(p)])
    return f"Showing {p.name} in Explorer."


@tool(summary="Open folder {name}")
def open_folder(name: Annotated[str, Field(description="A known folder ('downloads', 'desktop', 'documents', 'pictures', 'videos', 'music', 'home') or a folder name to search for")]) -> str:
    """Open a user folder by name, or find a folder anywhere on the PC by name and open it."""
    kf = known_folder(name)
    if kf and kf.exists():
        os.startfile(str(kf))  # noqa: S606
        return f"Opened {kf.name or 'home'} folder."
    res = search_paths(name, kind="folder", limit=5)
    if not res:
        raise ToolError(f"No folder named '{name}' found.")
    best = res[0]
    if len(res) > 1 and fuzz.WRatio(name.lower(), best["name"].lower()) < 80:
        return "Several folders match; ask which one: " + "; ".join(r["path"] for r in res)
    os.startfile(best["path"])  # noqa: S606
    return f"Opened folder {best['path']}."


@tool(summary="Play {query}")
def play_media(
    query: Annotated[str, Field(description="Movie/song/video name, e.g. 'interstellar'")],
    media: Annotated[Literal["video", "audio", "any"], Field(description="video for movies/shows, audio for songs")] = "any",
) -> str:
    """Find a movie, show, video or song on this PC by name and play it in the default player."""
    exts = VIDEO_EXT | AUDIO_EXT if media == "any" else (VIDEO_EXT if media == "video" else AUDIO_EXT)
    kind: Kind = "video" if media == "video" else ("audio" if media == "audio" else "any")
    res = search_paths(query, kind=kind, sort="largest", limit=10)
    res = [r for r in res if r["kind"] in exts]
    if not res:
        raise ToolError(f"I couldn't find any {media if media != 'any' else 'media'} named '{query}' on this PC.")
    # Prefer the largest file whose name fuzzy-matches best (full movie over trailer/sample).
    res.sort(key=lambda r: (-fuzz.partial_ratio(query.lower(), r["name"].lower()), r["size"]))
    best = res[0]
    os.startfile(best["path"])  # noqa: S606
    return f"Playing {best['name']}."

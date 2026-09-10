"""Browser & web tools."""

from __future__ import annotations

import webbrowser
from typing import Annotated, Literal
from urllib.parse import quote_plus

from pydantic import Field

from jarvis.tools.registry import tool

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "maps": "https://www.google.com/maps/search/{q}",
}

SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "netflix": "https://www.netflix.com",
    "prime video": "https://www.primevideo.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "drive": "https://drive.google.com",
    "docs": "https://docs.google.com",
    "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
}


def _open(url: str) -> None:
    webbrowser.open(url, new=2)


@tool(summary="Open {url}")
def open_url(url: Annotated[str, Field(description="Full URL, or a well-known site name like 'youtube', 'gmail', 'github'")]) -> str:
    """Open a website in the default browser."""
    key = url.strip().lower().removeprefix("www.")
    target = SITES.get(key) or (url if "://" in url else f"https://{url.strip()}")
    _open(target)
    return f"Opened {target}."


@tool(summary="Search web: {query}")
def web_search(
    query: Annotated[str, Field(description="What to search for")],
    engine: Annotated[Literal["google", "bing", "duckduckgo", "youtube", "github", "wikipedia", "amazon", "maps"], Field(description="Where to search")] = "google",
) -> str:
    """Open a web search for the query in the browser (Google by default; use engine='youtube' to search YouTube)."""
    _open(SEARCH_ENGINES[engine].format(q=quote_plus(query)))
    return f"Searching {engine} for '{query}'."

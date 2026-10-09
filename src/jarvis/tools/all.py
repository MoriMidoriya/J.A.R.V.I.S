"""Import every built-in tool module so they register into the default registry."""

from jarvis.tools import (  # noqa: F401
    apps,
    browser,
    files,
    inputs,
    media,
    memory,
    network,
    power,
    reminders,
    screen,
    shell,
    system,
    web,
    windows,
)
from jarvis.tools.registry import registry

__all__ = ["registry"]

"""Import every built-in tool module so they register into the default registry."""

from jarvis.tools import apps, browser, files, system  # noqa: F401
from jarvis.tools.registry import registry

__all__ = ["registry"]

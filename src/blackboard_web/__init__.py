"""Local web dashboard over the Blackboard MCP tools."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("blackboard-mcp")
except PackageNotFoundError:
    # A source tree that was never installed, or a frozen bundle built without
    # its dist-info. Neither is worth crashing over; the shell only reports it.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]

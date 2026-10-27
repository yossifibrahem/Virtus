"""Host source paths for explicit Docker mounts.

Container destinations and MCP arguments are always container paths. Host path
conversion must never imply mounting a directory or rewriting tool arguments.
"""
from __future__ import annotations

import sys


def host_path_to_docker_src(path_str: str) -> str:
    """Use Docker Desktop's forward-slash source syntax on Windows."""
    return path_str.replace("\\", "/") if sys.platform == "win32" else path_str


def parse_volume_source(spec: str) -> str:
    """Extract a volume source without splitting a Windows drive-letter colon."""
    source, separator, remainder = spec.partition(":")
    if separator and len(source) == 1 and source.isalpha():
        return source + ":" + remainder.partition(":")[0]
    return source

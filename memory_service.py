"""Persistent cross-chat memory service.

Owns the memory.md file at ~/.lumen/memory.md. The file is mounted
read-write into every conversation container at /memory.md so the model
can read and update it using configured file tools when available.

Other modules should import from here rather than touching the file directly:
  - container_service.py  calls ensure_file() to get the host path for mounting
  - chat_turn_service.py  calls read() to inject memory into the system prompt
"""
from __future__ import annotations

from pathlib import Path

MEMORY_FILE = Path.home() / ".lumen" / "memory.md"
CONTAINER_PATH = "/memory.md"

_TEMPLATE = """\
# Memory

## User

- **Name**:
- **Location**:
- **Occupation**:

## Preferences

- **Communication style**:
- **Language**:
- **Preferred tools / stack**:

## Ongoing Projects

## Notes
"""


def ensure_file() -> Path:
    """Create memory.md from the template if it doesn't exist. Returns its host path."""
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(_TEMPLATE)
    return MEMORY_FILE


def read() -> str:
    """Read bounded UTF-8 memory; the writable container mount is untrusted."""
    try:
        if MEMORY_FILE.exists():
            with MEMORY_FILE.open('rb') as source:
                raw = source.read(32769)
            text = raw[:32768].decode('utf-8', errors='replace').strip()
            return text + ('\n[Memory truncated at 32 KiB]' if len(raw) > 32768 else '')
    except OSError:
        pass
    return ""
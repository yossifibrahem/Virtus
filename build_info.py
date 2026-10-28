"""Application build metadata shared by the web server and desktop launcher."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


SOURCE_VERSION = "0.1.0-dev"
LOCAL_SANDBOX_IMAGE = "virtus-sandbox:latest"
_SANDBOX_FIXED_INPUTS = (
    ".dockerignore",
    "Dockerfile.sandbox",
    "sandbox_tools.py",
    "sandbox_files.py",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_root() -> Path:
    """Return the directory containing bundled read-only application assets."""
    if is_frozen():
        frozen_root = Path(getattr(sys, "_MEIPASS"))
        # PyInstaller's macOS bundle layout keeps real data files in
        # Contents/Resources and exposes symlinks from Contents/Frameworks.
        # Docker build contexts cannot follow those symlinks outside the
        # context directory, so use the canonical data location directly.
        resources_root = frozen_root.parent / "Resources"
        if frozen_root.name == "Frameworks" and resources_root.is_dir():
            return resources_root
        return frozen_root
    return Path(__file__).resolve().parent


def canonical_image_reference(image: str) -> str:
    """Return an explicit Docker reference without rewriting stored config."""
    reference = str(image).strip()
    if not reference or "@" in reference:
        return reference
    final_component = reference.rsplit("/", 1)[-1]
    return reference if ":" in final_component else f"{reference}:latest"


def sandbox_input_files(root: Path | None = None) -> list[Path]:
    """Return every bundled input that can affect the sandbox image."""
    context_root = Path(root) if root is not None else resource_root()
    paths = [context_root / relative for relative in _SANDBOX_FIXED_INPUTS]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        names = [str(path) for path in missing]
        raise FileNotFoundError(f"Sandbox build inputs are missing: {', '.join(names)}")
    return paths


def sandbox_identity(root: Path | None = None) -> str:
    """Hash Docker build inputs with platform-independent paths and newlines."""
    context_root = Path(root) if root is not None else resource_root()
    digest = hashlib.sha256()
    for path in sandbox_input_files(context_root):
        relative = path.relative_to(context_root).as_posix().encode("utf-8")
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _bundled_metadata() -> dict:
    if not is_frozen():
        return {}
    path = resource_root() / "build_metadata.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


_METADATA = _bundled_metadata()
APP_VERSION = str(
    os.getenv("VIRTUS_APP_VERSION")
    or _METADATA.get("version")
    or SOURCE_VERSION
).strip()
DEFAULT_SANDBOX_IMAGE = str(
    _METADATA.get("sandbox_image")
    or LOCAL_SANDBOX_IMAGE
).strip()
DESKTOP_PORT = int(os.getenv("VIRTUS_DESKTOP_PORT", "38492"))
APP_ID = "virtus-ai-chat"
CONTAINER_BUILD_LABEL = "com.virtus.sandbox.image"
CONTAINER_IDENTITY_LABEL = "com.virtus.sandbox.identity"

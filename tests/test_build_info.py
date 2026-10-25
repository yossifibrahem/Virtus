from __future__ import annotations

from pathlib import Path
import re
import sys

import pytest

import build_info


def test_source_build_defaults_to_local_first_run_image():
    if not build_info.is_frozen():
        assert build_info.DEFAULT_SANDBOX_IMAGE == "lumen-sandbox:latest"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("lumen-sandbox", "lumen-sandbox:latest"),
        ("lumen-sandbox:7", "lumen-sandbox:7"),
        ("registry.example:5000/lumen/sandbox", "registry.example:5000/lumen/sandbox:latest"),
        ("registry.example/lumen/sandbox@sha256:abc", "registry.example/lumen/sandbox@sha256:abc"),
    ],
)
def test_canonical_image_reference(value, expected):
    assert build_info.canonical_image_reference(value) == expected


def _write_sandbox_context(root: Path, newline: str = "\n") -> None:
    files = {
        ".dockerignore": "**\n!Dockerfile.sandbox\n",
        "Dockerfile.sandbox": "FROM ubuntu:24.04\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.replace("\n", newline).encode("utf-8"))


def test_sandbox_identity_is_platform_line_ending_independent(tmp_path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    _write_sandbox_context(lf, "\n")
    _write_sandbox_context(crlf, "\r\n")

    assert build_info.sandbox_identity(lf) == build_info.sandbox_identity(crlf)


def test_sandbox_identity_changes_only_for_build_inputs(tmp_path):
    root = tmp_path / "context"
    _write_sandbox_context(root)
    original = build_info.sandbox_identity(root)

    (root / "desktop_launcher.py").write_text("desktop-only change", encoding="utf-8")
    assert build_info.sandbox_identity(root) == original

    (root / "Dockerfile.sandbox").write_text("FROM ubuntu:26.04\n", encoding="utf-8")
    assert build_info.sandbox_identity(root) != original


@pytest.mark.parametrize(
    "relative",
    [
        ".dockerignore",
        "Dockerfile.sandbox",
    ],
)
def test_every_sandbox_build_input_changes_identity(tmp_path, relative):
    root = tmp_path / "context"
    _write_sandbox_context(root)
    original = build_info.sandbox_identity(root)

    with (root / relative).open("a", encoding="utf-8") as changed:
        changed.write("\nidentity-change")

    assert build_info.sandbox_identity(root) != original


def test_source_resource_root_contains_web_assets():
    assert (build_info.resource_root() / "templates" / "index.html").is_file()
    assert (build_info.resource_root() / "static" / "js" / "app.js").is_file()
    assert (build_info.resource_root() / "Dockerfile.sandbox").is_file()


def test_frozen_macos_resource_root_uses_real_resources_directory(monkeypatch, tmp_path):
    frameworks = tmp_path / "Contents" / "Frameworks"
    resources = tmp_path / "Contents" / "Resources"
    frameworks.mkdir(parents=True)
    resources.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)

    assert build_info.resource_root() == resources


@pytest.mark.parametrize("relative", [".dockerignore", "Dockerfile.sandbox"])
def test_missing_sandbox_input_reports_required_file(tmp_path, relative):
    _write_sandbox_context(tmp_path)
    (tmp_path / relative).unlink()

    with pytest.raises(FileNotFoundError, match=re.escape(relative)):
        build_info.sandbox_identity(tmp_path)

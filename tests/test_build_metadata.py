from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging" / "write_build_metadata.py"
SPEC = importlib.util.spec_from_file_location("virtus_write_build_metadata", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1.0-alpha.1", ("0.1.0", "0.1.0a1")),
        ("1.2.3-beta.4", ("1.2.3", "1.2.3b4")),
        ("2.0.0-rc.2", ("2.0.0", "2.0.0fc2")),
        ("0.1.0-dev", ("0.1.0", "0.1.0d1")),
        ("3.4.5", ("3.4.5", "3.4.5")),
    ],
)
def test_macos_bundle_versions(version, expected):
    assert metadata.macos_bundle_versions(version) == expected


def test_explicit_ci_build_number_takes_priority():
    assert metadata.macos_bundle_versions("0.1.0-alpha.2", "184") == ("0.1.0", "184")


def test_invalid_ci_build_number_is_rejected():
    with pytest.raises(ValueError, match="Invalid macOS build number"):
        metadata.macos_bundle_versions("0.1.0", "build-1")


def test_invalid_release_version_is_rejected():
    with pytest.raises(ValueError, match="Unsupported release version"):
        metadata.macos_bundle_versions("0.1.0-alpha.1-extra")

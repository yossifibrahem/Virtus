"""Generate frozen-app metadata from release environment variables."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "generated" / "build_metadata.json"
DEFAULT = ROOT / "packaging" / "build_metadata.json"
_SEMVER_PATTERN = re.compile(
    r"^(?P<core>\d+\.\d+\.\d+)(?:-(?P<label>alpha|beta|rc|dev)(?:[.-]?(?P<number>\d+))?)?$"
)


def macos_bundle_versions(version: str, build_number: str = "") -> tuple[str, str]:
    """Return Apple-compatible short and build versions for a release string."""
    match = _SEMVER_PATTERN.match(version.strip())
    if not match:
        raise ValueError(f"Unsupported release version: {version!r}")

    short_version = match.group("core")
    explicit_build = build_number.strip()
    if explicit_build:
        if not re.fullmatch(r"\d+(?:\.\d+){0,2}", explicit_build):
            raise ValueError(f"Invalid macOS build number: {explicit_build!r}")
        return short_version, explicit_build

    label = match.group("label")
    if not label:
        return short_version, short_version
    suffix = {"alpha": "a", "beta": "b", "rc": "fc", "dev": "d"}[label]
    prerelease_number = match.group("number") or "1"
    return short_version, f"{short_version}{suffix}{prerelease_number}"


def main() -> None:
    data = json.loads(DEFAULT.read_text(encoding="utf-8"))
    version = os.getenv("VIRTUS_BUILD_VERSION", "").strip()
    if version:
        data["version"] = version
    short_version, bundle_version = macos_bundle_versions(
        str(data["version"]),
        os.getenv("VIRTUS_BUILD_NUMBER", ""),
    )
    data["bundle_short_version"] = short_version
    data["bundle_version"] = bundle_version
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

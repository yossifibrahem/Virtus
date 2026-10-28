"""Generate the PyInstaller version resource for the Windows executable."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "build" / "generated" / "build_metadata.json"
OUTPUT = ROOT / "build" / "generated" / "windows_version_info.txt"


def main() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    version = str(metadata["version"])
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Unsupported Windows release version: {version!r}")
    build_number = os.getenv("VIRTUS_BUILD_NUMBER", "").strip()
    build = int(build_number) if build_number.isdigit() else 0
    numbers = (*[int(value) for value in match.groups()], build)
    numeric = ", ".join(str(value) for value in numbers)
    dotted = ".".join(str(value) for value in numbers)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}),
    prodvers=({numeric}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Virtus'),
         StringStruct('FileDescription', 'Virtus AI Chat'),
         StringStruct('FileVersion', '{dotted}'),
         StringStruct('InternalName', 'Virtus AI Chat'),
         StringStruct('OriginalFilename', 'Virtus AI Chat.exe'),
         StringStruct('ProductName', 'Virtus AI Chat'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    OUTPUT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()

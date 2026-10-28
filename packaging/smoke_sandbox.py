#!/usr/bin/env python3
"""Check the sandbox's Python runtime and workspace mount without an MCP server."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import docker_cli  # noqa: E402
from docker_path_utils import host_path_to_docker_src  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="virtus-sandbox:latest")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="virtus-sandbox-smoke-") as workspace:
        completed = docker_cli.run([
            "run", "--rm",
            "--volume", f"{host_path_to_docker_src(workspace)}:/workspace:rw",
            "--entrypoint", "python", args.image,
            "-c", (
                "from pathlib import Path; "
                "import httpx, pandas, requests; "
                "assert Path.cwd() == Path('/workspace'); "
                "Path('/workspace/virtus-smoke.txt').write_text('sandbox-ok')"
            ),
        ])
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "Sandbox smoke failed").strip())
        if (Path(workspace) / "virtus-smoke.txt").read_text() != "sandbox-ok":
            raise RuntimeError("Sandbox workspace write did not reach the host mount")

    print(json.dumps({"image": args.image, "python_runtime": True, "workspace_write": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

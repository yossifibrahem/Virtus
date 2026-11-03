#!/usr/bin/env python3
"""Check the sandbox's Python runtime and workspace mount without an MCP server."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import docker_cli  # noqa: E402
  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="virtus-sandbox:latest")
    args = parser.parse_args()

    volume = 'virtus-smoke-' + uuid.uuid4().hex
    try:
        created = docker_cli.run(['volume', 'create', volume])
        if created.returncode:
            raise RuntimeError(created.stderr)
        for code in ("from pathlib import Path; import httpx,pandas,requests; assert Path.cwd() == Path('/workspace'); Path('/workspace/virtus-smoke.txt').write_text('sandbox-ok')",
                     "from pathlib import Path; assert Path('/workspace/virtus-smoke.txt').read_text() == 'sandbox-ok'"):
            completed = docker_cli.run(['run', '--rm', '--mount', f'type=volume,src={volume},dst=/workspace',
                '--entrypoint', 'python', args.image, '-c', code])
            if completed.returncode:
                raise RuntimeError(completed.stderr or completed.stdout)
    finally:
        removed = docker_cli.run(['volume', 'rm', volume])
        if removed.returncode:
            raise RuntimeError(removed.stderr)

    print(json.dumps({"image": args.image, "python_runtime": True, "workspace_write": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

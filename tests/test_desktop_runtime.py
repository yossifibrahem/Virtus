import io
import json

import pytest

import desktop_runtime


@pytest.mark.parametrize("payload, expected", [({"app": "lumen-ai-chat"}, {"app": "lumen-ai-chat"}), ([], None), ("invalid", None)])
def test_health_probe_bounds_reads_and_accepts_only_objects(monkeypatch, payload, expected):
    body = b"not json" if payload == "invalid" else json.dumps(payload).encode()

    class Response(io.BytesIO):
        def read(self, size=-1):
            assert size == 4096
            return super().read(size)

    monkeypatch.setattr(desktop_runtime.urllib.request, "urlopen", lambda *args, **kwargs: Response(body))
    assert desktop_runtime.health_payload("http://127.0.0.1:38492") == expected


def test_health_wait_does_not_start_a_probe_after_deadline(monkeypatch):
    ticks = iter([0, 0.2, 0.8, 1.0])
    monkeypatch.setattr(desktop_runtime.time, "monotonic", lambda: next(ticks))
    sleeps = []
    monkeypatch.setattr(desktop_runtime.time, "sleep", sleeps.append)
    probes = []
    monkeypatch.setattr(desktop_runtime, "health_payload", lambda url, timeout: probes.append(timeout))
    assert not desktop_runtime.wait_for_health("http://127.0.0.1:38492", timeout=1)
    assert probes == [0.8]
    assert sleeps == [pytest.approx(0.2)]


def test_resource_validation_includes_both_sandbox_workers(monkeypatch, tmp_path):
    for relative in ("templates/index.html", "static/js/app.js", ".dockerignore", "Dockerfile.sandbox", "sandbox_tools.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(desktop_runtime.build_info, "resource_root", lambda: tmp_path)
    with pytest.raises(FileNotFoundError, match="sandbox_files.py"):
        desktop_runtime.validate_frozen_runtime()

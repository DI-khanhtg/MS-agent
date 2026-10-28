"""Exercise the documented CLI in an isolated source copy without developer credentials."""

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager

import pytest

from enterprise_agent.config import PROJECT_ROOT, Settings


def request(port, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def free_port():
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


@contextmanager
def running(project, environment, command, port):
    python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with (project / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(python), "-m", "enterprise_agent.main", command, "--port", str(port)],
            cwd=project,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(150):
                if process.poll() is not None:
                    pytest.fail((project / "server.log").read_text(encoding="utf-8")[-1500:])
                try:
                    request(port, "/health")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail("The isolated server did not start")
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(shutil.which("uv") is None, reason="Fresh installation check requires uv")
def test_clean_checkout_demo_history_and_microsoft_bootstrap(tmp_path):
    project = tmp_path / "enterprise-agent"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md", ".python-version"):
        shutil.copy2(PROJECT_ROOT / name, project / name)
    for name in ("src", "config", "templates"):
        shutil.copytree(
            PROJECT_ROOT / name,
            project / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(PROJECT_ROOT / "data/documents/Project-X", project / "data/documents/Project-X")
    environment = os.environ.copy()
    for key in list(environment):
        if key.casefold() in Settings.model_fields or key in {
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "ENTERPRISE_AGENT_ENV_FILE",
        }:
            environment.pop(key)
    environment["PYTHONUTF8"] = "1"
    result = subprocess.run(
        ["uv", "run", "--offline", "--no-dev", "enterprise-agent", "doctor", "--json"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["ready"] is False
    assert not (project / ".env").exists()
    port = free_port()
    with running(project, environment, "demo", port):
        config = request(port, "/api/config")
        assert config["runtime_mode"] == "demo"
        assert config["auth_enabled"] is False
        chat = request(
            port,
            "/api/chat",
            {
                "message": "Read folder Project-X and create proposal markdown",
                "session_id": "fresh-onboarding",
            },
        )
        assert chat["message"]
        assert request(port, "/api/artifacts")
    with running(project, environment, "demo", port):
        assert any(
            item["session_id"] == "fresh-onboarding" for item in request(port, "/api/sessions")
        )
    assert not (project / ".env").exists()

    port = free_port()
    values = {
        "GEMINI_API_KEY": "fixture-key-not-real",
        "AUTH_ENABLED": "true",
        "AZURE_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
        "AZURE_CLIENT_SECRET": "fixture-secret-not-real",
        "ENTRA_API_AUDIENCE": "22222222-2222-2222-2222-222222222222",
        "AUTH_ALLOWED_TENANT_IDS": "11111111-1111-1111-1111-111111111111",
        "ENTRA_ALLOWED_CLIENT_IDS": "33333333-3333-3333-3333-333333333333",
        "ENTRA_SPA_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
        "ENTRA_SPA_REDIRECT_URI": f"http://localhost:{port}/auth/callback",
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(port),
        "STORAGE_BACKEND": "database",
        "ENABLED_TOOL_GROUPS": "workiq_read,local_read,artifact_generation",
    }
    (project / ".env").write_text(
        "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()), encoding="utf-8"
    )
    with running(project, environment, "start", port):
        auth = request(port, "/api/auth/config")
        assert auth["enabled"] and auth["configured"]
        assert auth["client_id"] == values["ENTRA_SPA_CLIENT_ID"]

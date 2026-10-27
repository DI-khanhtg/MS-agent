"""Local setup, diagnostics, demonstration and server console commands."""

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from pydantic import ValidationError

from enterprise_agent.config import PROJECT_ROOT, Settings, get_settings
from enterprise_agent.onboarding import ConfigurationCheck, configuration_report, setup_environment


def _port(value: str) -> int:
    try:
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except ValueError:
        raise argparse.ArgumentTypeError("Use a port from 1 to 65535") from None


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


def serve(settings: Settings, *, open_browser: bool = False, reload: bool = False) -> None:
    """Run the existing ASGI application; preserve env settings for reload subprocesses."""
    redirect = urlsplit(settings.entra_spa_redirect_uri)
    host = (
        redirect.hostname
        if settings.auth_enabled and redirect.hostname in {"localhost", "127.0.0.1"}
        else "localhost"
    )
    url = (
        settings.public_base_url
        if settings.deployment_mode == "shared"
        else f"http://{host}:{settings.app_port}"
    )
    with socket.socket() as probe:
        if os.name != "nt":
            # Match Uvicorn's reuse behavior when restarting after TCP TIME_WAIT.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((settings.app_host, settings.app_port))
    print(f"Open {url} | Stop with Ctrl+C")
    if open_browser:
        threading.Thread(
            target=_open_when_ready, args=(url, settings.app_port), daemon=True
        ).start()
    get_settings.cache_clear()
    uvicorn.run(
        "enterprise_agent.api.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=reload,
        workers=1,
    )


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    setup = commands.add_parser("setup", help="Create private owner configuration interactively")
    setup.add_argument(
        "--shared", action="store_true", help="Configure one centrally hosted backend"
    )
    setup.add_argument(
        "--uploads", action="store_true", help="Opt into account-isolated server uploads"
    )
    setup.add_argument(
        "--output", type=Path, help="New private env file; never overwrites an existing file"
    )
    doctor = commands.add_parser("doctor", help="Check configuration offline; never prints secrets")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable checks")
    doctor.add_argument(
        "--env-file", type=Path, help="Read an operator-selected private config file"
    )
    for command, help_text in (("start", "Run local chat"), ("demo", "Try without keys or login")):
        child = commands.add_parser(command, help=help_text)
        child.add_argument("--open", action="store_true", dest="open_browser", help="Open browser")
        child.add_argument("--port", type=_port, help="Must match the registered SPA redirect")
        child.add_argument("--reload", action="store_true", help="Reload code during development")
        if command == "start":
            child.add_argument("--env-file", type=Path, help="Read private server configuration")
    return parser.parse_args(argv)


@contextmanager
def _temporary_environment(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.command == "setup":
        return setup_environment(
            args.output or PROJECT_ROOT / ".env", shared=args.shared, uploads=args.uploads
        )
    overrides = {}
    if args.command == "demo":
        overrides = {
            "APP_ENV": "development",
            "APP_HOST": "127.0.0.1",
            "RUNTIME_MODE": "demo",
            "AUTH_ENABLED": "false",
            "ENABLED_TOOL_GROUPS": "local_read,artifact_generation",
            "STORAGE_BACKEND": "database",
            "SPEECH_PROVIDER": "disabled",
            "DATABASE_URL": "",
            "DATABASE_PATH": "data/demo.db",
            "ARTIFACT_STORAGE_PATH": "data/artifacts/demo",
            "AUDIT_LOG_PATH": "data/demo-audit.jsonl",
            "TELEMETRY_ENABLED": "false",
            "APP_PORT": "8000",
            "LOCAL_DOCUMENTS_ROOT": "data/documents",
            "ALLOWED_HOSTS": "localhost,127.0.0.1",
            "ENTERPRISE_AGENT_ENV_FILE": "",
            "DEPLOYMENT_MODE": "local",
            "AUTH_TENANT_MODE": "allowlist",
            "DOCUMENT_UPLOADS_ENABLED": "false",
            "PUBLIC_BASE_URL": "",
        }
    if getattr(args, "env_file", None) is not None:
        overrides["ENTERPRISE_AGENT_ENV_FILE"] = str(args.env_file.resolve())
    if getattr(args, "port", None) is not None:
        overrides["APP_PORT"] = str(args.port)
    with _temporary_environment(overrides):
        return _configured_command(args)


def _configured_command(args: argparse.Namespace) -> int:
    settings = None
    try:
        env_file = os.environ.get("ENTERPRISE_AGENT_ENV_FILE")
        if env_file and not Path(env_file).is_file():
            raise FileNotFoundError("The selected server configuration file is missing")
        settings = Settings() if env_file is None else Settings(_env_file=env_file or None)
        if getattr(args, "port", None) is not None:
            settings = settings.model_copy(update={"app_port": args.port})
        report = configuration_report(settings)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        report = [
            ConfigurationCheck(
                "invalid_settings",
                "error",
                "Invalid fields: " + ", ".join(fields) + ". Check .env; values are hidden.",
            )
        ]
    except OSError:
        report = [
            ConfigurationCheck(
                "configuration_unreadable",
                "error",
                "Cannot read the selected configuration file. Check its path and permissions.",
            )
        ]
    failed = any(check.level == "error" for check in report)
    if args.command == "doctor" and args.json:
        print(
            json.dumps(
                {
                    "ready": not failed,
                    "live_verified": False,
                    "checks": [asdict(check) for check in report],
                },
                indent=2,
            )
        )
    elif args.command == "doctor" or failed:
        for check in report:
            print(f"[{check.level.upper()}] {check.code}: {check.message}")
        if not failed:
            print("Configuration ready (offline). Sign in to verify live access.")
    if failed:
        return 2
    if args.command == "doctor":
        return 0
    assert settings is not None
    try:
        serve(
            settings,
            open_browser=getattr(args, "open_browser", False),
            reload=getattr(args, "reload", False),
        )
    except OSError:
        print(
            "Cannot bind the configured host/port. Stop the other local instance, or choose "
            "another --port and update the Entra SPA redirect to match."
        )
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


def run() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())


if __name__ == "__main__":
    run()

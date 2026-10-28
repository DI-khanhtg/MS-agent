import importlib
import json
import os
from dataclasses import asdict

import pytest

from enterprise_agent.config import Settings

TENANT = "11111111-1111-1111-1111-111111111111"
BACKEND = "22222222-2222-2222-2222-222222222222"
FRONTEND = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def clean_config_environment(monkeypatch):
    for name in list(os.environ):
        if name.casefold() in Settings.model_fields:
            monkeypatch.delenv(name)


def onboarding():
    return importlib.import_module("enterprise_agent.onboarding")


def configured(**overrides):
    values = dict(
        gemini_api_key="private-gemini-value",
        auth_enabled=True,
        azure_tenant_id=TENANT,
        azure_client_id=BACKEND,
        azure_client_secret="private-entra-value",
        entra_api_audience=BACKEND,
        auth_allowed_tenant_ids=TENANT,
        entra_allowed_client_ids=FRONTEND,
        entra_spa_client_id=FRONTEND,
        enabled_tool_groups="workiq_read,local_read,artifact_generation",
        storage_backend="database",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_setup_writes_private_parseable_config_and_account_specific_defaults(tmp_path):
    values = iter(["", TENANT, BACKEND, FRONTEND])
    secrets = iter(["gemini-#'\"\\$value", "entra-#'\"\\$value"])
    output = []
    target = tmp_path / ".env"
    assert (
        onboarding().setup_environment(
            target,
            input_fn=lambda _: next(values),
            secret_fn=lambda _: next(secrets),
            output_fn=output.append,
        )
        == 0
    )
    settings = Settings(_env_file=target)
    assert settings.gemini_api_key.get_secret_value() == "gemini-#'\"\\$value"
    assert settings.azure_client_secret.get_secret_value() == "entra-#'\"\\$value"
    assert settings.allowed_tenant_ids == {TENANT}
    assert settings.allowed_client_ids == {FRONTEND}
    assert settings.storage_backend == "database"
    assert settings.app_host == "127.0.0.1"
    assert settings.auth_enabled
    assert "workiq_write" not in settings.tool_groups
    assert not any("$value" in line for line in output)
    if os.name != "nt":
        assert target.stat().st_mode & 0o077 == 0


def test_setup_never_overwrites_existing_credentials_or_prompts(tmp_path):
    target = tmp_path / ".env"
    target.write_text("original private config", encoding="utf-8")

    def unexpected(_):
        pytest.fail("Existing config must not be replaced or requested again")

    assert (
        onboarding().setup_environment(
            target, input_fn=unexpected, secret_fn=unexpected, output_fn=lambda _: None
        )
        == 0
    )
    assert target.read_text() == "original private config"


def test_cancelled_setup_leaves_no_partial_env_file(tmp_path):
    def cancelled(_):
        raise EOFError

    target = tmp_path / ".env"
    assert (
        onboarding().setup_environment(target, secret_fn=cancelled, output_fn=lambda _: None) == 130
    )
    assert not target.exists()


def test_invalid_tenant_and_empty_secrets_are_reprompted(tmp_path):
    values = iter(["", "common", TENANT, BACKEND, FRONTEND])
    secrets = iter(["", "TO_BE_FILLED_LATER", "private-key", "private-secret"])
    target = tmp_path / ".env"
    assert (
        onboarding().setup_environment(
            target,
            input_fn=lambda _: next(values),
            secret_fn=lambda _: next(secrets),
            output_fn=lambda _: None,
        )
        == 0
    )
    assert Settings(_env_file=target).azure_tenant_id == TENANT


def test_doctor_accepts_independent_tenant_installations_without_wildcards():
    module = onboarding()
    first = configured()
    second_tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = configured(azure_tenant_id=second_tenant, auth_allowed_tenant_ids=second_tenant)
    for settings in (first, second):
        report = module.configuration_report(settings)
        assert not [check for check in report if check.level == "error"]
    assert first.allowed_tenant_ids.isdisjoint(second.allowed_tenant_ids)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"gemini_api_key": ""}, "gemini_key"),
        ({"azure_client_secret": ""}, "workiq_credentials"),
        ({"auth_enabled": False}, "authentication_required"),
        ({"azure_tenant_id": "common"}, "entra_ids"),
        ({"auth_allowed_tenant_ids": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}, "entra_tenant"),
        ({"entra_allowed_client_ids": BACKEND}, "entra_client"),
        ({"app_port": 8001}, "redirect_port"),
    ],
)
def test_doctor_reports_specific_configuration_problems_without_secrets(overrides, code):
    report = onboarding().configuration_report(configured(**overrides))
    assert any(check.level == "error" and check.code == code for check in report)
    serialized = json.dumps([asdict(check) for check in report])
    assert "private-gemini-value" not in serialized
    assert "private-entra-value" not in serialized


def test_cli_default_reports_missing_key_before_starting_server(monkeypatch, capsys):
    from enterprise_agent import main

    monkeypatch.setattr(main, "Settings", lambda: Settings(_env_file=None))
    assert main.main([]) == 2
    output = capsys.readouterr().out
    assert "setup" in output
    assert "gemini_key" in output


def test_demo_is_process_only_and_disables_live_credentials(monkeypatch):
    from enterprise_agent import main

    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-value")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ENABLED_TOOL_GROUPS", "workiq_read,workiq_write")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:password@unreachable.example/db")
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("ALLOWED_HOSTS", "agent.company.example")
    monkeypatch.setattr(main, "Settings", lambda **kwargs: Settings(_env_file=None))
    seen = []
    monkeypatch.setattr(main, "serve", lambda settings, **kwargs: seen.append(settings))
    assert main.main(["demo"]) == 0
    assert seen[0].runtime_mode == "demo"
    assert not seen[0].auth_enabled
    assert seen[0].tool_groups == {"local_read", "artifact_generation"}
    assert seen[0].speech_provider == "disabled"
    assert seen[0].database_url.get_secret_value() == ""
    assert not seen[0].telemetry_enabled
    assert seen[0].allowed_hosts == "localhost,127.0.0.1"
    assert os.environ["AUTH_ENABLED"] == "true"
    assert os.environ["ENABLED_TOOL_GROUPS"] == "workiq_read,workiq_write"


def test_demo_ignores_live_env_for_both_launcher_and_asgi_settings(tmp_path, monkeypatch):
    from enterprise_agent import main
    from enterprise_agent.config import get_settings

    live_env = tmp_path / ".env"
    original = "GEMINI_TIMEOUT_SECONDS=invalid-private-value\nALLOWED_HOSTS=agent.company.example\n"
    live_env.write_text(original, encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", live_env)
    seen = []

    def fake_serve(settings, **kwargs):
        seen.append(settings)
        assert get_settings().allowed_hosts == "localhost,127.0.0.1"
        assert get_settings().app_port == 8000

    monkeypatch.setattr(main, "serve", fake_serve)
    assert main.main(["demo"]) == 0
    assert seen[0].app_port == 8000
    assert live_env.read_text(encoding="utf-8") == original


def test_start_does_not_launch_when_port_disagrees_with_registered_callback(monkeypatch):
    from enterprise_agent import main

    monkeypatch.setattr(main, "Settings", lambda: configured())
    assert main.main(["start", "--port", "8001"]) == 2


def test_doctor_json_is_safe_even_when_settings_cannot_be_parsed(monkeypatch, capsys):
    from enterprise_agent import main

    def invalid_settings():
        return Settings(_env_file=None, app_port="private-invalid-value")

    monkeypatch.setattr(main, "Settings", invalid_settings)
    assert main.main(["doctor", "--json"]) == 2
    output = capsys.readouterr().out
    assert "private-invalid-value" not in output
    assert json.loads(output)["ready"] is False


def test_port_conflict_is_actionable_and_never_starts_second_server(monkeypatch, capsys):
    import socket

    from enterprise_agent import main

    with socket.socket() as occupied:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        settings = configured(
            app_host="127.0.0.1",
            app_port=port,
            entra_spa_redirect_uri=f"http://localhost:{port}/auth/callback",
        )
        monkeypatch.setattr(main, "Settings", lambda: settings)
        monkeypatch.setattr(main.uvicorn, "run", lambda *a, **kw: pytest.fail("Port is occupied"))
        assert main.main(["start"]) == 2
        assert "another --port" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="POSIX TIME_WAIT restart semantics")
def test_server_can_restart_after_recent_connection_close(monkeypatch):
    import socket

    from enterprise_agent import main

    with socket.socket() as old:
        old.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        old.bind(("127.0.0.1", 0))
        old.listen()
        port = old.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = old.accept()
            with connection:
                connection.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
    called = []
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **kw: called.append(kw))
    main.serve(configured(app_host="127.0.0.1", app_port=port))
    assert called[0]["port"] == port

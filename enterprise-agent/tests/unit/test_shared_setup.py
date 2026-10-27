import os

from enterprise_agent.config import Settings
from enterprise_agent.hosting import shared_configuration_errors
from enterprise_agent.onboarding import configuration_report, setup_environment


def test_owner_shared_wizard_creates_server_config_not_end_user_keys(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.casefold() in Settings.model_fields:
            monkeypatch.delenv(key)
    inputs = iter(
        [
            "",
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
            "https://agent.example.com",
        ]
    )
    secrets = iter(["server-only-gemini", "server-only-entra"])
    target = tmp_path / ".env.shared"
    assert (
        setup_environment(
            target,
            shared=True,
            input_fn=lambda _: next(inputs),
            secret_fn=lambda _: next(secrets),
            output_fn=lambda _: None,
        )
        == 0
    )
    settings = Settings(_env_file=target)
    assert settings.deployment_mode == "shared"
    assert settings.auth_tenant_mode == "organizations"
    assert settings.entra_spa_redirect_uri == "https://agent.example.com/auth/callback"
    assert settings.storage_backend == "database"
    assert not settings.document_uploads_enabled
    assert "local_read" not in settings.tool_groups
    assert not shared_configuration_errors(settings)
    assert not [check for check in configuration_report(settings) if check.level == "error"]


def test_cli_can_read_explicit_server_config_file(tmp_path, monkeypatch, capsys):
    from enterprise_agent import main

    target = tmp_path / ".env.shared"
    target.write_text("RUNTIME_MODE=demo\nSTORAGE_BACKEND=database\n", encoding="utf-8")
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    assert main.main(["doctor", "--env-file", str(target), "--json"]) == 0
    assert '"ready": true' in capsys.readouterr().out
    assert "ENTERPRISE_AGENT_ENV_FILE" not in os.environ


def test_explicit_missing_server_config_does_not_fall_back_to_defaults(tmp_path, capsys):
    import json

    from enterprise_agent import main

    assert main.main(["doctor", "--env-file", str(tmp_path / "missing.env"), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["checks"][0]["code"] == "configuration_unreadable"

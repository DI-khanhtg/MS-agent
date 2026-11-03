"""Offline review probes; all generated application data lives in a temporary directory."""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

os.environ["ENTERPRISE_AGENT_ENV_FILE"] = ""
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult, MicrosoftAgentRuntime
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import Principal


class FixtureRuntime:
    answer = ""

    async def chat(self, message, session_id, context=None):
        return AgentResult(self.answer, [])

    async def stream_chat(self, message, session_id, context=None):
        yield self.answer


def emit(kind, **values):
    print(json.dumps({"probe": kind, **values}, ensure_ascii=False), flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix="enterprise-review-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            runtime_mode="demo",
            enabled_tool_groups="local_read,artifact_generation",
            artifact_storage_path=root / "artifacts",
            audit_log_path=root / "audit.jsonl",
            local_documents_root=root / "documents",
        )
        gate = EgressGate.from_yaml(settings.egress_policy_path)
        # Synthetic values only; compare dictionary keys with ordinary field values.
        for value in (
            {"alice@example.test": "report"},
            {"Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAA": "ok"},
            {"text": "alice@example.test"},
            {"text": "Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        ):
            decision = gate.evaluate(value)
            emit("egress", input=value, allowed=decision.allowed, payload=decision.payload)

        runtime = FixtureRuntime()
        app = create_app(settings=settings, runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as client:
            for kind in ("markdown", "json", "docx", "xlsx", "pptx", "pdf"):
                response = client.post(
                    "/api/artifacts",
                    json={
                        "artifact_type": kind,
                        "title": "   ",
                        "sections": [{"heading": "Summary", "body": "Review fixture"}],
                    },
                )
                emit("blank_title", format=kind, status=response.status_code)

            artifact = client.post(
                "/api/artifacts",
                json={
                    "artifact_type": "markdown",
                    "title": "Report",
                    "sections": [{"heading": "Summary", "body": "Review fixture"}],
                },
            ).json()
            url = artifact["download_url"]
            runtime.answer = "Analysis. " * 500 + "\n\n[Download](" + url + ")"
            for endpoint, session_id in (
                ("/api/chat", "long-complete"),
                ("/api/chat/stream", "long-stream"),
            ):
                response = client.post(
                    endpoint, json={"message": "Show report", "session_id": session_id}
                )
                saved = client.get("/api/sessions/" + session_id).json()["messages"][-1]["text"]
                emit(
                    "long_history",
                    endpoint=endpoint,
                    status=response.status_code,
                    response_has_link=url in response.text,
                    saved_has_link=url in saved,
                    answer_chars=len(runtime.answer),
                    saved_chars=len(saved),
                )

        message = "a" * 4500 + " UNIQUE_FINAL_INSTRUCTION"
        safe = MicrosoftAgentRuntime(settings)._sanitize_input(message, Principal.anonymous())
        emit(
            "long_input",
            input_chars=len(message),
            model_input_chars=len(safe),
            final_instruction_preserved="UNIQUE_FINAL_INSTRUCTION" in safe,
        )

        wheel = next((Path(__file__).parent / "review-dist").glob("*.whl"))
        with ZipFile(wheel) as archive:
            names = archive.namelist()
            emit(
                "wheel_resources",
                present={
                    name: any(entry.endswith(name) for entry in names)
                    for name in ("config/egress.yaml", "config/approvals.yaml", "config/artifacts.yaml")
                },
            )
            archive.extractall(root / "installed")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "installed")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from enterprise_agent.config import Settings; "
                "from enterprise_agent.security.egress import EgressGate; "
                "EgressGate.from_yaml(Settings(_env_file=None).egress_policy_path)",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        emit(
            "installed_wheel",
            returncode=result.returncode,
            missing_egress_policy="FileNotFoundError" in result.stderr and "egress.yaml" in result.stderr,
        )


if __name__ == "__main__":
    main()

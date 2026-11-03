import copy

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.tools.document_formats import DocumentExtractionError, DocumentLimits
from enterprise_agent.tools.document_sandbox import (
    SandboxClient,
    validate_container,
    verify_attestation,
)


def sandbox_settings():
    return Settings(
        _env_file=None, document_sandbox_enabled=True, document_sandbox_image="sha256:" + "a" * 64
    )


def inspected(settings):
    return {
        "Image": settings.document_sandbox_image,
        "Config": {
            "User": "10001:10001",
            "Entrypoint": ["/opt/venv/bin/python", "-m", "enterprise_agent.tools.document_worker"],
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges=true"],
            "Memory": settings.document_memory_mb * 1024**2,
            "MemorySwap": settings.document_memory_mb * 1024**2,
            "NanoCpus": int(settings.document_cpus * 1e9),
            "PidsLimit": settings.document_pids_limit,
            "Binds": [],
            "Devices": [],
            "PidMode": "",
            "IpcMode": "private",
            "LogConfig": {"Type": "none"},
            "Tmpfs": {
                "/work": f"rw,nosuid,nodev,noexec,size={settings.document_tmpfs_mb}m,"
                "uid=10001,gid=10001,mode=700"
            },
        },
        "Mounts": [],
    }


def attestation(settings):
    return {
        "protocol": 1,
        "ready": True,
        "uid": 10001,
        "network_interfaces": ["lo"],
        "cap_eff": 0,
        "no_new_privs": 1,
        "seccomp": 2,
        "root_readonly": True,
        "memory_max": settings.document_memory_mb * 1024**2,
        "pids_max": settings.document_pids_limit,
        "cpu_quota": 100000,
        "cpu_period": 100000,
        "cpu_limit": settings.document_cpu_seconds,
        "file_limit": settings.document_max_expanded_bytes,
        "address_limit": settings.document_memory_mb * 4 * 1024**2,
        "work_tmpfs": True,
    }


def test_container_configuration_and_kernel_attestation_must_both_pass():
    settings = sandbox_settings()
    validate_container(inspected(settings), settings)
    verify_attestation(attestation(settings), settings)


@pytest.mark.parametrize(
    "key,value",
    [
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("NanoCpus", 0),
        ("PidsLimit", 0),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("CapAdd", ["SYS_ADMIN"]),
        ("Binds", ["/:/host"]),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
        ("PidMode", "host"),
        ("IpcMode", "host"),
    ],
)
def test_unsafe_runtime_configuration_is_rejected(key, value):
    settings = sandbox_settings()
    value_doc = inspected(settings)
    value_doc["HostConfig"][key] = value
    with pytest.raises(DocumentExtractionError, match="[Ss]andbox"):
        validate_container(value_doc, settings)


@pytest.mark.parametrize(
    "key,value",
    [
        ("network_interfaces", ["lo", "eth0"]),
        ("uid", 0),
        ("cap_eff", 1),
        ("no_new_privs", 0),
        ("seccomp", 0),
        ("root_readonly", False),
        ("memory_max", "max"),
        ("pids_max", "max"),
        ("cpu_quota", "max"),
        ("cpu_limit", -1),
        ("work_tmpfs", False),
    ],
)
def test_missing_kernel_enforcement_is_rejected(key, value):
    settings = sandbox_settings()
    proof = copy.deepcopy(attestation(settings))
    proof[key] = value
    with pytest.raises(DocumentExtractionError, match="[Ss]andbox"):
        verify_attestation(proof, settings)


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private parser contents")])
def test_job_timeout_or_parser_crash_removes_container_and_sanitizes_error(monkeypatch, failure):
    settings = sandbox_settings()
    client = SandboxClient(settings)
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        return [inspected(settings)] if args[0] == "inspect" else "created"

    monkeypatch.setattr(client, "_command", command)
    monkeypatch.setattr(client, "_exchange", lambda *args: (_ for _ in ()).throw(failure))
    with pytest.raises(DocumentExtractionError) as error:
        client.extract(b"From: a@example.test\n\nbody", ".eml", DocumentLimits())
    assert "private" not in str(error.value)
    assert any(command[:2] == ["rm", "--force"] for command in commands)
    create = commands[0]
    assert "--network=none" in create and "--log-driver=none" in create
    assert not any("--volume" in arg or "--mount" in arg for arg in create)


def test_invalid_kernel_proof_receives_no_document_bytes(monkeypatch):
    import io
    import json

    from enterprise_agent.tools import document_sandbox

    settings = sandbox_settings()
    proof = attestation(settings)
    proof["network_interfaces"] = ["lo", "eth0"]
    writes = []

    class Input:
        closed = False

        def write(self, data):
            writes.append(data)

        def close(self):
            self.closed = True

    class Process:
        stdin = Input()
        stdout = io.BytesIO(json.dumps(proof).encode() + b"\n")
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(document_sandbox.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(DocumentExtractionError):
        SandboxClient(settings)._exchange("job", b"PRIVATE", ".eml", DocumentLimits())
    assert writes == []


def test_watchdog_terminates_a_stalled_stdin_writer_and_removes_job(monkeypatch):
    import json
    import sys
    import time

    from enterprise_agent.tools import document_sandbox

    settings = sandbox_settings().model_copy(update={"document_timeout_seconds": 0.3})
    real_popen = document_sandbox.subprocess.Popen
    program = (
        "import time; print("
        + repr(json.dumps(attestation(settings)))
        + ", flush=True); time.sleep(60)"
    )
    processes, commands = [], []

    def start(*args, **kwargs):
        process = real_popen([sys.executable, "-u", "-c", program], **kwargs)
        processes.append(process)
        return process

    client = SandboxClient(settings)

    def command(arguments, **kwargs):
        commands.append(arguments)
        return [inspected(settings)] if arguments[0] == "inspect" else "created"

    monkeypatch.setattr(document_sandbox.subprocess, "Popen", start)
    monkeypatch.setattr(client, "_command", command)
    started = time.monotonic()
    with pytest.raises(DocumentExtractionError):
        client.extract(b"x" * 1_000_000, ".eml", DocumentLimits())
    assert time.monotonic() - started < 5
    assert processes and all(process.poll() is not None for process in processes)
    assert any(arguments[:2] == ["rm", "--force"] for arguments in commands)


def test_success_is_not_returned_when_container_cleanup_fails(monkeypatch):
    client = SandboxClient(sandbox_settings())

    def command(arguments, **kwargs):
        if arguments[0] == "inspect":
            return [inspected(client.settings)]
        if arguments[0] == "rm":
            raise DocumentExtractionError("Sandbox cleanup failed")
        return "still-present"

    monkeypatch.setattr(client, "_command", command)
    monkeypatch.setattr(
        client,
        "_exchange",
        lambda *args: {
            "ok": True,
            "document": {"text": "safe", "warnings": [], "format": "eml"},
        },
    )
    with pytest.raises(DocumentExtractionError, match="cleanup"):
        client.extract(b"file", ".eml", DocumentLimits())

"""Fail-closed Docker transport for isolated, bounded document-processing jobs."""

import base64
import json
import queue
import subprocess
import threading
import time
from uuid import uuid4

from enterprise_agent.tools.document_formats import CONVERSIONS, DocumentExtractionError

_ENTRYPOINT = ["/opt/venv/bin/python", "-m", "enterprise_agent.tools.document_worker"]
_POOLS = {}
_POOL_LOCK = threading.Lock()


def worker_arguments(settings):
    return [
        "--wall",
        str(settings.document_timeout_seconds),
        "--cpu",
        str(settings.document_cpu_seconds),
        "--memory",
        str(settings.document_memory_mb * 1024**2),
        "--pids",
        str(settings.document_pids_limit),
        "--file-limit",
        str(settings.document_max_expanded_bytes),
    ]


def validate_container(value, settings):
    try:
        host, config = value["HostConfig"], value["Config"]
        expected_image = settings.document_sandbox_image
        image_ok = (
            value["Image"] == expected_image
            if expected_image.startswith("sha256:")
            else config.get("Image") == expected_image
        )
        tmpfs = host.get("Tmpfs", {})
        work = set(tmpfs.get("/work", "").split(","))
        expected_memory = settings.document_memory_mb * 1024**2
        valid = (
            image_ok
            and config["User"] == "10001:10001"
            and config["Entrypoint"] == _ENTRYPOINT
            and host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False
            and not host.get("CapAdd")
            and {cap.upper() for cap in host["CapDrop"]} == {"ALL"}
            and any(
                opt in {"no-new-privileges", "no-new-privileges=true"}
                for opt in host["SecurityOpt"]
            )
            and not any("unconfined" in opt for opt in host["SecurityOpt"])
            and host["Memory"] == expected_memory
            and host["MemorySwap"] == expected_memory
            and host["NanoCpus"] == int(settings.document_cpus * 1e9)
            and host["PidsLimit"] == settings.document_pids_limit
            and not host.get("Binds")
            and not host.get("Devices")
            and not host.get("PortBindings")
            and host.get("PidMode", "") == ""
            and host["IpcMode"] == "private"
            and host["LogConfig"]["Type"] == "none"
            and set(tmpfs) == {"/work"}
            and {"rw", "nosuid", "nodev", "noexec", f"size={settings.document_tmpfs_mb}m"} <= work
            and all(
                mount.get("Type") == "tmpfs" and mount.get("Destination") == "/work"
                for mount in value.get("Mounts", [])
            )
        )
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DocumentExtractionError("Sandbox runtime configuration failed verification") from None


def verify_attestation(value, settings):
    try:
        positive = (
            "memory_max",
            "pids_max",
            "cpu_quota",
            "cpu_period",
            "cpu_limit",
            "file_limit",
            "address_limit",
        )
        if any(type(value[key]) is not int or value[key] <= 0 for key in positive):
            raise ValueError
        valid = (
            value["protocol"] == 1
            and value["ready"] is True
            and value["uid"] == 10001
            and value["network_interfaces"] == ["lo"]
            and value["cap_eff"] == 0
            and value["no_new_privs"] == 1
            and value["seccomp"] == 2
            and value["root_readonly"] is True
            and value["work_tmpfs"] is True
            and value["memory_max"] <= settings.document_memory_mb * 1024**2
            and value["pids_max"] <= settings.document_pids_limit
            and value["cpu_quota"] / value["cpu_period"] <= settings.document_cpus + 0.00001
            and value["cpu_limit"] <= settings.document_cpu_seconds
            and value["file_limit"] <= settings.document_max_expanded_bytes
            and value["address_limit"] <= settings.document_memory_mb * 4 * 1024**2
        )
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        raise DocumentExtractionError("Sandbox kernel limits failed verification") from None


class SandboxClient:
    def __init__(self, settings):
        self.settings = settings

    def _command(self, arguments, *, as_json=False):
        try:
            result = subprocess.run(
                [self.settings.document_sandbox_runtime, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=True,
                shell=False,
            )
            if len(result.stdout) > 1_000_000:
                raise ValueError
            return json.loads(result.stdout) if as_json else result.stdout.decode().strip()
        except Exception:
            raise DocumentExtractionError("Sandbox runtime is unavailable or failed") from None

    def _create_arguments(self, name):
        settings = self.settings
        memory = settings.document_memory_mb * 1024**2
        return [
            "create",
            "--interactive",
            "--name",
            name,
            "--pull=never",
            "--label=enterprise-agent.document-job=true",
            "--network=none",
            "--ipc=private",
            "--cgroupns=private",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--user=10001:10001",
            "--log-driver=none",
            f"--memory={memory}",
            f"--memory-swap={memory}",
            f"--cpus={settings.document_cpus}",
            f"--pids-limit={settings.document_pids_limit}",
            f"--ulimit=cpu={settings.document_cpu_seconds}:{settings.document_cpu_seconds}",
            f"--ulimit=as={memory * 4}:{memory * 4}",
            f"--ulimit=fsize={settings.document_max_expanded_bytes}:{settings.document_max_expanded_bytes}",
            "--ulimit=nofile=256:256",
            "--ulimit=core=0:0",
            "--stop-timeout=1",
            "--tmpfs",
            f"/work:rw,nosuid,nodev,noexec,size={settings.document_tmpfs_mb}m,uid=10001,gid=10001,mode=700",
            "--env=HOME=/work",
            "--env=TMPDIR=/work",
            settings.document_sandbox_image,
            *worker_arguments(settings),
        ]

    def _exchange(self, name, content, suffix, limits):
        process = subprocess.Popen(
            [self.settings.document_sandbox_runtime, "start", "--attach", "--interactive", name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        messages = queue.Queue(maxsize=3)
        output_limit = limits.max_chars * 6 + 40_000
        deadline = time.monotonic() + self.settings.document_timeout_seconds
        timed_out = threading.Event()

        def kill():
            timed_out.set()
            process.kill()
            try:
                self._command(["rm", "--force", "--volumes", name])
            except DocumentExtractionError:
                pass  # The outer finally retries and requires confirmed removal.

        def read():
            try:
                for _ in range(2):
                    line = process.stdout.readline(output_limit + 1)
                    if not line or len(line) > output_limit or not line.endswith(b"\n"):
                        raise ValueError
                    messages.put(json.loads(line))
            except Exception:
                messages.put(None)

        reader = threading.Thread(target=read, daemon=True)
        watchdog = threading.Timer(self.settings.document_timeout_seconds, kill)
        watchdog.daemon = True
        reader.start()
        watchdog.start()
        try:

            def receive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                value = messages.get(timeout=remaining)
                if value is None:
                    raise ValueError
                return value

            proof = receive()
            verify_attestation(proof, self.settings)
            # No document bytes are sent until both runtime and kernel policy passed.
            payload = {
                "protocol": 1,
                "suffix": suffix,
                "limits": limits.to_dict(),
                "conversion_enabled": self.settings.document_conversion_enabled,
                "content": base64.b64encode(content).decode("ascii"),
            }
            process.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            process.stdin.close()
            result = receive()
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if process.returncode or timed_out.is_set():
                raise TimeoutError if timed_out.is_set() else ValueError
            return result
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
            reader.join(timeout=1)

    def extract(self, content, suffix, limits):
        from enterprise_agent.tools.document_reader import ExtractedDocument

        settings = self.settings
        if not settings or not settings.document_sandbox_enabled:
            raise DocumentExtractionError("Sandbox is required for advanced document formats")
        if suffix in CONVERSIONS and not settings.document_conversion_enabled:
            raise DocumentExtractionError("Document conversion is disabled by configuration")
        with _POOL_LOCK:
            pool = _POOLS.setdefault(
                settings.document_max_concurrent_jobs,
                threading.BoundedSemaphore(settings.document_max_concurrent_jobs),
            )
        if not pool.acquire(timeout=settings.document_timeout_seconds):
            raise DocumentExtractionError("Sandbox processing capacity is busy; retry later")
        name = "enterprise-document-" + uuid4().hex
        created = False
        try:
            # Attempt cleanup even if creation times out after the daemon created it.
            created = True
            self._command(self._create_arguments(name))
            inspected = self._command(["inspect", name], as_json=True)
            if not isinstance(inspected, list) or len(inspected) != 1:
                raise ValueError
            validate_container(inspected[0], settings)
            result = self._exchange(name, content, suffix, limits)
            if result.get("ok") is not True:
                errors = {
                    "invalid_document": "Document could not be extracted safely",
                    "timeout": "Document conversion exceeded the time limit",
                    "conversion_unavailable": "LibreOffice conversion is unavailable",
                }
                raise DocumentExtractionError(
                    errors.get(result.get("code"), "Sandbox parser failed")
                )
            document = result["document"]
            text, warnings = document["text"], document["warnings"]
            headers, units = document.get("headers"), document.get("unit_count")
            converted = document.get("converted_to")
            if (
                not isinstance(text, str)
                or len(text) > limits.max_chars
                or not isinstance(warnings, list)
                or len(warnings) > 20
                or any(not isinstance(warning, str) or len(warning) > 1024 for warning in warnings)
                or document["format"] != suffix[1:]
                or (
                    units is not None
                    and (type(units) is not int or not 0 <= units <= limits.max_units)
                )
                or (converted is not None and converted != CONVERSIONS.get(suffix, "")[1:])
            ):
                raise ValueError
            if headers is not None and (
                not isinstance(headers, dict)
                or not set(headers) <= {"from", "to", "cc", "date", "subject"}
                or any(
                    not isinstance(value, str) or len(value) > 2048 for value in headers.values()
                )
            ):
                raise ValueError
            return ExtractedDocument(text, suffix[1:], units, tuple(warnings), headers, converted)
        except DocumentExtractionError:
            raise
        except (TimeoutError, subprocess.TimeoutExpired, queue.Empty):
            raise DocumentExtractionError(
                "Sandbox document processing exceeded the time limit"
            ) from None
        except Exception:
            raise DocumentExtractionError("Sandbox document processing failed safely") from None
        finally:
            try:
                if created:
                    self._remove(name)
            finally:
                pool.release()

    def _remove(self, name):
        try:
            self._command(["rm", "--force", "--volumes", name])
        except DocumentExtractionError:
            # A watchdog or daemon may already have removed it. Distinguish absence from
            # unavailable inspection; successful `ps` with no match proves cleanup.
            remaining = self._command(["ps", "--all", "--quiet", "--filter", f"name=^{name}$"])
            if remaining:
                raise DocumentExtractionError(
                    "Sandbox cleanup failed; operator attention required"
                ) from None

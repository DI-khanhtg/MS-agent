"""Single-job worker entrypoint; refuses source input without kernel isolation."""

import argparse
import base64
import contextlib
import json
import logging
import os
import signal
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from enterprise_agent.tools.document_formats import (
    CONVERSIONS,
    SUPPORTED,
    DocumentExtractionError,
    DocumentLimits,
)


def kernel_attestation(args):
    # Imported here so Windows can import the pure parser functions in offline tests.
    import resource

    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    cgroup = Path("/sys/fs/cgroup")
    quota, period = (cgroup / "cpu.max").read_text().split()
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    return {
        "protocol": 1,
        "ready": True,
        "uid": os.getuid(),
        "network_interfaces": sorted(path.name for path in Path("/sys/class/net").iterdir()),
        "cap_eff": int(status["CapEff"].strip(), 16),
        "no_new_privs": int(status["NoNewPrivs"]),
        "seccomp": int(status["Seccomp"]),
        "root_readonly": bool(os.statvfs("/").f_flag & os.ST_RDONLY),
        "work_tmpfs": any(line.split()[4] == "/work" and " - tmpfs " in line for line in mounts),
        "memory_max": int((cgroup / "memory.max").read_text()),
        "pids_max": int((cgroup / "pids.max").read_text()),
        "cpu_quota": int(quota),
        "cpu_period": int(period),
        "cpu_limit": resource.getrlimit(resource.RLIMIT_CPU)[0],
        "file_limit": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
        "address_limit": resource.getrlimit(resource.RLIMIT_AS)[0],
    }


def process_document(content, suffix, limits, directory, *, conversion_enabled=True, timeout=30):
    """Pure orchestration exercised by unit tests; production calls it only after attestation."""
    from enterprise_agent.tools.document_reader import ExtractedDocument, extract_native
    from enterprise_agent.tools.document_validation import validate_compound, validate_content

    validate_content(content, suffix, limits=limits)
    if suffix in {".doc", ".ppt", ".xls", ".msg"}:
        validate_compound(content, suffix, limits)
    if suffix in {".eml", ".msg"}:
        from enterprise_agent.tools.document_email import extract_email

        result = extract_email(
            content,
            suffix,
            max_chars=limits.max_chars,
            max_depth=limits.max_depth,
            max_parts=limits.max_mail_parts,
        )
        return ExtractedDocument(result.text, suffix[1:], 1, result.warnings, result.headers)
    if suffix in CONVERSIONS:
        if not conversion_enabled:
            raise DocumentExtractionError("Document conversion is disabled")
        from enterprise_agent.tools.document_conversion import convert_document

        output, converted = convert_document(
            content, suffix, directory, timeout=timeout, max_output_bytes=limits.max_expanded_bytes
        )
        # Reuse the exact DOCX/PPTX/XLSX parsers with bounded converted input.
        result = extract_native(
            output, converted, replace(limits, max_source_bytes=limits.max_expanded_bytes)
        )
        return replace(
            result,
            format=suffix[1:],
            converted_to=converted[1:],
            warnings=(
                *result.warnings,
                "Converted by LibreOffice; some layout or unsupported objects may be lost.",
            ),
        )
    return extract_native(content, suffix, limits)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wall", type=int, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--memory", type=int, required=True)
    parser.add_argument("--pids", type=int, required=True)
    parser.add_argument("--file-limit", type=int, required=True)
    args = parser.parse_args()
    output = sys.stdout

    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()

    def expired(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(min(args.wall, 180))
    try:
        from types import SimpleNamespace

        from enterprise_agent.tools.document_sandbox import verify_attestation

        proof = kernel_attestation(args)
        settings = SimpleNamespace(
            document_memory_mb=args.memory // 1024**2,
            document_pids_limit=args.pids,
            document_cpus=4,
            document_cpu_seconds=args.cpu,
            document_max_expanded_bytes=args.file_limit,
        )
        verify_attestation(proof, settings)
        emit(proof)
        line = sys.stdin.buffer.readline(28_000_001)
        if len(line) > 28_000_000 or not line.endswith(b"\n"):
            raise ValueError
        request = json.loads(line)
        if request.get("protocol") != 1 or request.get("suffix") not in SUPPORTED:
            raise ValueError
        limits = DocumentLimits(**request["limits"])
        upper = DocumentLimits(
            max_source_bytes=20_000_000,
            max_expanded_bytes=args.file_limit,
            max_archive_entries=10000,
            max_compression_ratio=1000,
            max_depth=64,
            max_units=2000,
            max_cells=1_000_000,
            max_mail_parts=1000,
            max_chars=1_000_000,
        )
        if any(
            type(value) is not int or not 0 < value <= getattr(upper, key)
            for key, value in limits.to_dict().items()
        ):
            raise ValueError
        content = base64.b64decode(request["content"], validate=True)
        logging.disable(logging.CRITICAL)
        # Suppress third-party diagnostics; stdout contains only the bounded protocol.
        with (
            tempfile.TemporaryDirectory(prefix="document-", dir="/work") as directory,
            open(os.devnull, "w") as silent,
            contextlib.redirect_stdout(silent),
            contextlib.redirect_stderr(silent),
        ):
            result = process_document(
                content,
                request["suffix"],
                limits,
                Path(directory),
                conversion_enabled=request.get("conversion_enabled") is True,
                timeout=args.wall,
            )
        emit({"ok": True, "document": asdict(result)})
    except TimeoutError:
        emit({"ok": False, "code": "timeout"})
    except Exception:
        # Never serialize exception messages, source paths, headers or parser tracebacks.
        emit({"ok": False, "code": "invalid_document"})
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()

# Advanced document processing (F5)

## Support matrix

The source of truth is `tools/document_formats.py`; the existing parser implementations
remain in `tools/document_reader.py`. There was no plugin/parser registry before F5.
Routing tables now distinguish native extraction, LibreOffice conversion and email parsing.
`GET /api/config` returns the enabled extensions used by the upload UI and local inventory.

| Extension | Before F5 | Processing after F5 | Preserved content / limitations |
| --- | --- | --- | --- |
| `.txt`, `.md`, `.csv`, `.json` | UTF-8 text | Existing text parser | Plain text; CSV/JSON are not interpreted as executable data or queried |
| `.docx` | python-docx | Same parser | Paragraph/table order, nested tables, headers and footers |
| `.pptx` | python-pptx | Same parser | Slides, grouped text, tables, speaker notes; images/charts are not read |
| `.xlsx` | openpyxl | Same parser | Sheet/cell order and cached values; formulas are not evaluated |
| `.pdf` | pypdf | Same parser | Selectable text and page coverage; no OCR |
| `.doc` | Rejected | LibreOffice → DOCX → existing parser | Readable document structure, subject to conversion fidelity |
| `.ppt` | Rejected | LibreOffice → PPTX → existing parser | Slide order, readable text/tables/notes where conversion preserves them |
| `.xls` | Rejected | LibreOffice → XLSX → existing parser | Cached/readable worksheet values; conversion may change unsupported features |
| `.rtf` | Rejected | LibreOffice → DOCX → existing parser | Readable paragraphs/tables; active fields and embedded objects rejected |
| `.odt` | Rejected | LibreOffice → DOCX → existing parser | Readable text/tables; scripts/events/macro libraries rejected |
| `.eml` | Rejected | Python stdlib email | From, To, Cc, Subject, Date, readable plain/HTML body |
| `.msg` | Rejected | extract-msg | Same email fields; no attachment extraction or saving |
| `.docm`, `.xlsm`, `.pptm`, macro templates/add-ins | Unsupported | Always rejected | No macro execution or automatic format downgrade |

**All seven new formats require the sandbox.** Disabled/unavailable/unverified isolation
never falls back to a host converter or inline email parser. With the sandbox disabled,
the eight existing formats keep their compatible native path. When enabled,
`DOCUMENT_SANDBOX_ALL_FORMATS=true` also routes existing formats through the same sandbox.
Set up and verify this mode for production document ingestion; native compatibility mode
alone is not a complete OS sandbox. Setting `DOCUMENT_CONVERSION_ENABLED=false` removes
conversion formats from advertised support while allowing isolated email extraction.

All extraction is text-only. Scans, images, charts, attachments, nested email messages,
encrypted documents and unsupported embedded objects are outside coverage. Conversion can
change layout, unsupported objects and cached spreadsheet values; metadata reports the
original format, converted format, method, unit count, warnings and text-only coverage.

## Isolation and process lifecycle

`tools/document_sandbox.py` creates a fresh, uniquely named Linux Docker container per job.
No source directories, Docker socket, tokens, backend environment or host secret volumes
are mounted into a worker. Document bytes travel through bounded stdin/stdout. Each worker
gets a private `/work` tmpfs and per-job directory; the root filesystem is read-only, the
user is `10001:10001`, capabilities are dropped and no-new-privileges/seccomp are required.
Runtime logs are disabled, and parser diagnostics never become API responses or audit data.

Isolation is checked twice **before sending document bytes**:

1. Inspect the created container: pinned image/entrypoint/user, no network, no host mounts,
   no privileged/device/capability additions, private IPC, read-only root, tmpfs and explicit
   CPU, memory/swap and PID limits.
2. Read the worker's startup attestation: actual Linux interfaces (loopback only), UID,
   effective capabilities, no-new-privileges, seccomp, read-only root, tmpfs, cgroup-v2
   `memory.max`, `cpu.max`, `pids.max` and inherited CPU/address-space/file-size limits.

A rootless engine that ignores requested resource flags fails these checks. Cgroup v2 and
proper systemd delegation are required for rootless Docker resource limits; see
[Docker rootless resource limits](https://docs.docker.com/engine/security/rootless/tips/).
Docker's [none network driver](https://docs.docker.com/engine/network/drivers/none/) creates
only loopback; this is verified again from inside the worker.

The host watchdog covers the attached worker and blocked stdin/stdout, force-removes timed
out jobs, and waits for CLI termination. The worker also has its own wall-clock alarm and
inherited process limits. Successful extraction is not returned until container removal is
confirmed. Crashes, invalid protocol, parser exceptions, oversized output and cleanup errors
produce fixed diagnostics without bodies, headers, tokens, filenames or tracebacks.
Concurrency is bounded per API process; account for the aggregate across deployment workers.

LibreOffice uses a fresh profile and a local UNO pipe in headless mode. Every document load
sets `MacroExecutionMode=NEVER_EXECUTE`, `UpdateDocMode=NO_UPDATE`, hidden/read-only mode and
disabled link updates. The profile also disables macros and has no trusted locations.
Conversion output is validated again before using the existing native parser. Headless
startup alone is not considered a macro policy or sandbox. See
[LibreOffice startup options](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html).

## Input validation and privacy

Upload declarations are checked against an allowlist of MIME types; empty/generic
`application/octet-stream` declarations are allowed only when content validation succeeds.
Extensions and actual containers are checked independently: PDF/RTF signatures, Office
package parts/content types, OpenDocument mimetype, email header structure and compound-file
magic. OLE stream type/macro checks run inside the worker before conversion or MSG parsing.

Archive validation bounds entries, combined expanded bytes, compression ratio and embedded
package depth. It counts central-directory records before allocating `ZipInfo` objects, so
forged ZIP entry counts do not bypass limits. Traversal, links, encrypted packages, malformed
archives, macro streams and unsupported embedded executable objects are rejected. Encoding-
aware streaming XML callbacks reject DTDs/entities and ODT event scripts, including UTF-16.
Parser limits also bound slides/pages/sheets, worksheet cells, document structure and MIME/HTML
depth/part counts. Kernel limits remain mandatory for advanced parsing, including malformed
parser inputs that cannot be rejected cheaply by preflight.

Email parsing prefers readable plain text, otherwise converts HTML into plain text without
browser execution, URL fetching, active attributes, scripts, styles, SVG, forms or frames.
Attachments and nested messages are not saved or recursively extracted. `extract-msg` is
used only inside the sandbox with delayed attachment initialization.

Headers and body remain untrusted document data. Local and Work IQ readers classify the
complete extracted text **and metadata** before cutting a window, mask PII across the whole
document, then apply the existing egress policy to the result. Continuation offsets index
sanitized text, including any expansion from redaction. Extracted fields never become a
system/developer message. Existing identity-scoped handles and per-turn source caches remain
in place. F3 request locking waits for in-flight processing/cleanup before local user erasure.

## Configuration

All variables are backend settings; clients cannot select images, runtime commands or limits.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCUMENT_ENABLED_FORMATS` | All 15 listed extensions | Explicit format allowlist; unsupported/macro extensions fail validation |
| `DOCUMENT_SANDBOX_ENABLED` | `false` | Enable verified container processing; required for advanced formats |
| `DOCUMENT_SANDBOX_IMAGE` | empty | Immutable `sha256:<image-id>` or `repository@sha256:<digest>`; required when enabled |
| `DOCUMENT_SANDBOX_RUNTIME` | `docker` | Trusted Docker CLI executable, invoked without a shell |
| `DOCUMENT_SANDBOX_ALL_FORMATS` | `true` | Also isolate the existing formats when the sandbox is enabled |
| `DOCUMENT_CONVERSION_ENABLED` | `true` | Allow LibreOffice conversion |
| `DOCUMENT_TIMEOUT_SECONDS` | `45` | Worker/host processing deadline |
| `DOCUMENT_CPU_SECONDS` | `30` | Per-process inherited CPU limit |
| `DOCUMENT_CPUS` | `1.0` | Cgroup CPU quota per job |
| `DOCUMENT_MEMORY_MB` | `512` | Cgroup memory cap; swap limit requests the same total |
| `DOCUMENT_TMPFS_MB` | `128` | Private job tmpfs capacity |
| `DOCUMENT_PIDS_LIMIT` | `64` | Cgroup task/process/thread cap |
| `DOCUMENT_MAX_CONCURRENT_JOBS` | `2` | Per-process admission bound |
| `DOCUMENT_MAX_SOURCE_BYTES` | `5000000` | Standard input bound |
| `DOCUMENT_CLOUD_MAX_SOURCE_BYTES` | `20000000` | Explicit large Graph file bound, within existing absolute cap |
| `DOCUMENT_UPLOAD_MAX_BYTES` | `2000000` | Browser upload cap; can be lowered, retaining the existing 2 MB ceiling |
| `DOCUMENT_MAX_EXPANDED_BYTES` | `50000000` | Combined archive/converted-file bound |
| `DOCUMENT_MAX_ARCHIVE_ENTRIES` | `2000` | Combined ZIP/OLE entry bound |
| `DOCUMENT_MAX_COMPRESSION_RATIO` | `200` | Maximum declared ZIP expansion ratio |
| `DOCUMENT_MAX_DEPTH` | `32` | Document, package path and email nesting bound |
| `DOCUMENT_MAX_UNITS` | `200` | Page/slide/sheet count bound |
| `DOCUMENT_MAX_CELLS` | `100000` | Worksheet dimension/cell bound |
| `DOCUMENT_MAX_MAIL_PARTS` | `256` | MIME part bound |
| `DOCUMENT_MAX_CHARS` | `100000` | Extracted character bound; configurable up to one million |

Existing `LOCAL_MAX_FILE_BYTES`, file-count, API-body, source-read budget and egress limits
still apply. Raising one setting does not override other limits. The UI consumes the enabled
extensions and upload cap from `/api/config`; changing settings requires restarting workers.

## Build, installation and deployment

The backend's existing Dockerfile is not a converter image and does not install Docker CLI
or LibreOffice. `Dockerfile.documents` builds a separate worker with Debian system Python,
LibreOffice/UNO and the project parsers. A system-site-packages venv exposes Debian's UNO
binding; do not mix it with a different bundled Python ABI.

Optional `documents` dependencies are `extract-msg` (required MSG/MAPI body/header support)
and `olefile` (directly used for OLE type/macro preflight). They are also development test
dependencies. The backend transport does not need these extras or LibreOffice installed
locally; only the isolated worker does. No new hosted API/provider is introduced.

From `enterprise-agent/`, using a local Linux Docker engine with cgroup v2:

```powershell
docker build -f Dockerfile.documents -t enterprise-document-worker:local .
$documentImageId = docker image inspect enterprise-document-worker:local --format '{{.Id}}'
$env:DOCUMENT_SANDBOX_IMAGE = $documentImageId.Trim()
$env:DOCUMENT_SANDBOX_ENABLED = 'true'
$env:DOCUMENT_SANDBOX_ALL_FORMATS = 'true'
uv run python scripts/check_document_sandbox.py
```

The probe uses synthetic email only and fails unless runtime policy, kernel enforcement,
bounded extraction and cleanup all succeed. Build/pull images during deployment, then pin
their immutable ID/digest. Jobs use `--pull=never`; they do not download images at request
time. Rebuild the image after changes to parsers, lockfile, LibreOffice or the worker protocol.

Use a dedicated, supported Linux/rootless Docker environment for the launcher. Granting an
API process access to a rootful Docker socket grants host-level control; the implementation
does not silently mount that socket or provision a daemon. A containerized backend needs a
deliberately configured restricted launcher/daemon connection and Docker CLI; the existing
backend image alone cannot enable F5. Worker containers themselves must never receive that
connection, socket, secrets or document-directory mounts. Keep the worker image and launcher
under administrator control and verify the probe on the actual deployment.

This development host is Windows and had neither Docker/Podman nor LibreOffice installed.
Windows users need a functioning Linux-container engine/VM with the required controllers,
not just `soffice.exe` or WSL command availability. No local subprocess fallback is provided.
On Windows, stop the running CLI/service before `uv sync` if its executable is locked.

Worker jobs have the label `enterprise-agent.document-job=true`. Monitor failed cleanup and
resource/OOM failures. After a backend/daemon crash, inspect labelled stopped containers and
remove only confirmed abandoned job IDs; do not prune active work from other requests. The
worker alarm and cgroup limits provide additional bounds if the launching process exits.
Host/VM swap, snapshots and daemon storage remain subject to infrastructure encryption and
retention; tmpfs is not a claim of guaranteed physical secure erasure. See F3 operations for
backup/deletion limits. There is no persistent conversion cache shared across users.

## Offline tests and accuracy

```powershell
uv sync --extra dev --extra documents
uv run --extra dev pytest tests/unit tests/integration tests/security evals -q -m 'not live'
uv run --extra dev ruff check src tests scripts
uv lock --check --offline
```

All default tests are offline. In-process sandbox doubles exist only in tests, not as a
production setting. Real native parsers and stdlib email/extract-msg are measured on 22
self-authored fixtures: two per native format and three each for EML/MSG, including HTML
and Unicode. Every fixture must achieve at least 95% character agreement against independent
reference text using whitespace normalization and `SequenceMatcher(autojunk=False).ratio()`.
The current measured minimum/mean is 100% for that corpus; this is not a claim about arbitrary
customer files or unexecuted LibreOffice conversions.

Four self-authored RTF/ODT fixtures and three genuine DOC/PPT/XLS fixtures from Apache POI
extend the real-container corpus to 29 cases across all 15 extensions. Their provenance,
hashes and licenses are in `tests/fixtures/legacy/`. Conversion routing/reuse is tested with
explicit doubles by default; **actual conversion agreement and kernel enforcement require**:

```powershell
$env:RUN_DOCUMENT_SANDBOX_TESTS = '1'
uv run --extra dev --extra documents pytest tests/integration/test_document_sandbox_real.py -q
```

This opt-in suite must pass on the deployed image before enabling advanced formats. It runs
locally against pinned images and fixtures, calls no Microsoft/LLM services, and cannot be
counted as verified on a host without Docker. Default security tests cover macros, mismatches,
forged/oversized/nested archives, UTF-16 XML entities/scripts, malformed inputs, MIME/HTML
depth, timeout/crash handling, proof-before-stdin, stalled writes, cleanup failure, privacy,
pagination, user isolation and native parser regressions.

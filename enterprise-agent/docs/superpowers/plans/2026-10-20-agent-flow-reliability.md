# Agent Flow Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prevent artifact replay, make chat/stream deadlines consistent, tailor instructions to enabled capabilities, and expose execution timing without leaking content.

**Architecture:** Keep one Microsoft Agent Framework agent and the existing provider/tool adapters. A request-local budget bounds the turn across session waiting, model/tool execution, retries and saving. Preserve backend-only approval and record artifact attempts before creation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Settings, agent-framework-openai 1.14.3, existing OpenAI-compatible DeepSeek client, pytest/pytest-asyncio, Ruff.

**Spec:** [Approved flow review](../../FLOW_REVIEW.md), first reliability increment only.

## Global Constraints

- No new dependency, external service, index, model, or agent.
- Keep self-hosted FastAPI, DeepSeek and the current Microsoft Agent Framework pin.
- Keep API/SSE event names and response shapes; timing goes to the existing audit channel.
- Keep demo/deepseek modes, JWT/OBO isolation, egress policy and backend-only approval.
- Never replay a turn after any recorded tool attempt or emitted stream content.
- Work IQ mutations remain single-attempt; do not change the existing MCP retry fixes.
- Do not edit `.env`, print secrets, or call live providers in verification.
- Preserve pre-existing uncommitted files. Do not stage or commit unrelated work.
- No indexing, extraction cache, evidence ledger or durable workflow resume in this increment.
- Current branch is `feat/local-enterprise-agent`; inspect its dirty state again before execution.

## Implemented Configuration Contract

These defaults implement the approved first increment; they are not measured performance targets:

| Setting | Default / validation | Meaning |
| --- | --- | --- |
| `AGENT_TURN_TIMEOUT_SECONDS` | 300; `0 < value <= 1800` | One wall-clock budget for the whole turn, including retry backoff and session-lock waiting. |
| `AGENT_SYNTHESIS_RESERVE_SECONDS` | 15; `0 <= value < AGENT_TURN_TIMEOUT_SECONDS` | Reserve before the final deadline during which new enterprise reads are stopped, allowing the model to explain incomplete coverage. |
| `DEEPSEEK_TIMEOUT_SECONDS` | Existing 60; existing validation | Provider HTTP request timeout, no longer the timeout for the whole tool loop. |
| Work IQ/Fabric timeouts | Existing 30/120 | Per-adapter settings remain; outer request budget caps aggregate source work and retries. |

The SDK client must receive an explicitly configured `AsyncOpenAI` client with
`timeout=deepseek_timeout_seconds` and `max_retries=0`. The application remains the
owner of retry policy, avoiding hidden SDK retries on top of runtime retries.
Its function-invocation duration uses the total turn budget, not the model HTTP timeout.

The source reserve is best effort: it gives synthesis time but cannot guarantee
that a slow provider will finish. At the final deadline return the existing timeout
error, never a fabricated successful or complete result.

## Review Focus

1. Failure after artifact creation must leave one file and must not trigger a second creation; test chat and stream.
2. A queue wait or retry backoff must not reset the total deadline; cancellation must not become a retried upstream error.
3. Two concurrent users/turns must not share a budget or recorder, and a source called outside a turn must preserve its configured behavior.
4. A disabled capability must not be recommended by the prompt; approval, egress and untrusted-data policy must remain active.
5. Timing/error audits must exclude input text, tool arguments, tokens and raw identity; stream errors must not emit a successful `done` event.

## Task 1: Record Real Artifact Attempts Before Creation

**Files:** Modify `src/enterprise_agent/artifacts/tools.py`; create
`tests/integration/test_artifact_runtime.py`.

**Interfaces:** Consumes existing `record_tool_call(name, arguments)`,
`ArtifactService.create(spec, principal)`, `MicrosoftAgentRuntime` and recorder budget.
Produces a recorded `create_artifact` attempt before any service side effect.

- [x] Write regressions using real `ArtifactTools`, service, renderer and temporary artifact storage. Inject only the model failure. Parameterize chat/stream and assert one stored artifact after a transient model error following tool completion.
- [x] Add a successful-turn case asserting `result.tool_calls[0]['name'] == 'create_artifact'` and downloaded/stored Markdown contains the requested content.
- [x] Add a tool-budget-exhausted case asserting no artifact is written. A direct artifact API call outside an agent turn must still work.
- [x] Run `python -m pytest tests/integration/test_artifact_runtime.py -q`; confirm failures expose missing recording/replay rather than fixture errors.
- [x] Record the attempt at the beginning of `ArtifactTools.create_artifact`, before awaiting the service. Record bounded descriptive metadata, not the full generated document body.
- [x] Re-run that file plus `tests/integration/test_artifact_api.py`, `tests/unit/test_operations.py` and `tests/unit/test_runtime.py`; all must pass.
- [x] Record changed files and verification results; leave unrelated working-tree changes untouched.

## Task 2: Separate Turn Budget, Provider Timeout and Source Windows

**Files:** Create `src/enterprise_agent/agent/budget.py` and
`tests/unit/test_turn_budget.py`; modify `config.py`, `agent/factory.py`,
`agent/runtime.py`, `tools/workiq/tools.py`, `tools/fabric/tools.py`,
`tools/knowledge/client.py`, `tests/unit/test_config.py`,
`tests/unit/test_runtime.py`, `tests/unit/test_factory.py` and affected source tests.
Paths without prefixes above are relative to `src/enterprise_agent` except tests.

**Interfaces:**
- `turn_budget(total_seconds: float, reserve_seconds: float)` is a synchronous
  context manager establishing/resetting request-local absolute monotonic deadlines.
- `remaining_read_seconds() -> float | None` returns remaining source time,
  returns `None` outside a turn and raises `TimeoutError` when the read window expires.
- Existing `chat`, `stream_chat`, tool signatures and public result models stay unchanged.

- [x] Write config validation for nonpositive total timeout and reserve greater than/equal to total. Include a valid fractional-second configuration for fast tests.
- [x] Write parameterized chat/stream tests proving an injected agent can run longer than the model HTTP timeout while staying inside the turn timeout. Move the existing timeout regression to the new turn setting.
- [x] Write deadline tests for session-lock waiting, retry backoff and repeated transient failures. Assert the provider/agent is not called again after the shared deadline.
- [x] Write budget isolation tests for concurrent turns, reset after failure/cancellation, no inherited budget outside the context, and a source blocked after reserve begins.
- [x] Write a source test where slow token acquisition plus MCP work/retries is capped by the remaining read window. Confirm source adapters still pass results through egress when successful.
- [x] Run the new and affected tests; observe expected failures before implementation.
- [x] Implement the two validated settings and request-local budget helper. Place one `asyncio.timeout` around session acquisition/lock, execution/backoff and save in each runtime path. Translate expiry to `AgentRequestTimeout`; preserve cancellation propagation and the no-replay checks.
- [x] Configure the provider HTTP timeout explicitly and disable SDK retries. Keep runtime attempts bounded by `deepseek_max_retries`, total remaining time and the existing safety checks.
- [x] Wrap enterprise read operations, including token acquisition and adapter retries, in the remaining read window. Keep the existing MCP client internals and mutation path unchanged.
- [x] Run `python -m pytest tests/unit/test_turn_budget.py tests/unit/test_runtime.py tests/unit/test_factory.py tests/unit/test_config.py tests/unit/test_workiq_tools.py tests/unit/test_fabric_tools.py tests/unit/test_knowledge.py tests/integration/test_agent_tool_loop.py -q`; all must pass.

## Task 3: Build Instructions From Enabled Capabilities

**Files:** Modify `agent/instructions.py`, `agent/workflows.py`,
`agent/factory.py`, `tests/unit/test_factory.py`, `tests/unit/test_workflows.py`
and add a request-contract assertion in `tests/integration/test_agent_tool_loop.py`.

**Interfaces:** `build_system_instructions(groups: set[str]) -> str` consumes
`Settings.tool_groups`. The factory continues exposing the same registry tools.

- [x] Write tests through the factory/request boundary for local-only, Work IQ-only, Fabric-only, mixed-source and action-enabled configurations. Disabled tool names must be absent from task-specific instructions; matching enabled instructions must be present.
- [x] Cover an empty tool configuration and a mock-only configuration: ordinary assistance remains possible, and unsupported enterprise retrieval is not offered.
- [x] Assert safety requirements remain across configurations: no invented company facts, untrusted retrieved content, egress enforcement, no consequential writes without backend approval, and no success claim after failed tools.
- [x] Run `python -m pytest tests/unit/test_workflows.py tests/unit/test_factory.py -q`; confirm missing capability filtering causes failures.
- [x] Split fixed safety policy, common task guidance and capability-specific guidance into explicit blocks. Compose them with `build_system_instructions`; do not filter by user-message keywords or add a model call for routing.
- [x] Update the factory to use the builder. Keep tool descriptions and registry enforcement authoritative; update legacy full-prompt tests to exercise enabled combinations.
- [x] Run the two unit files plus `tests/integration/test_agent_tool_loop.py`; all must pass with the same enabled tools as before.

## Task 4: Record Turn/Source Timing and Verify Complete Behavior

**Files:** Modify `telemetry/operations.py`, `agent/runtime.py`, the read adapters
from Task 2, `tests/unit/test_operations.py`, `tests/unit/test_runtime.py` and
`tests/integration/test_api.py`; update `docs/OPERATIONS.md`,
`docs/LOCAL_TESTING.md`, `docs/IMPLEMENTATION_STATUS.md` and `docs/FLOW_REVIEW.md`.

**Interfaces:** Keep `audit_event(event, **metadata)`; allow bounded numeric
`first_delta_ms`, integer `attempts` and fixed stage labels alongside existing
`latency_ms`/`status`. Use monotonic time for durations, not wall-clock timestamps.
Do not change `AgentResult`, `ChatResponse`, SSE events or UI handling.

- [x] Write tests verifying success/failure/timeout audits include elapsed time, stream first-delta timing is emitted only when text was emitted, and concurrent turn records remain isolated.
- [x] Include recognizable input/token/tool-content strings in fixtures and assert they are absent from serialized timing audits.
- [x] Write an API streaming regression asserting a failed/timed-out stream emits `error` without `done`; preserve any already-emitted text.
- [x] Run affected tests and observe failures for the missing timing behavior.
- [x] Emit one completion audit per runtime turn with status/duration/attempts and optional first-delta timing. Emit source read duration/status using fixed stage labels; never record raw query/path/result content in these events.
- [x] Document the new settings and changed meaning of `DEEPSEEK_TIMEOUT_SECONDS`. Distinguish artifact replay protection within a turn from idempotency across separate client requests.
- [x] Run `python -m pytest -m "not live" -q`, `python -m ruff check .` and `git diff --check`. Read complete summaries and report any skips/failures accurately.
- [x] Review the diff for approval bypass, changed tool availability, recorder/reset leaks, unexpected exception translation, session save behavior and accidentally included existing edits.
- [x] Record test totals and remaining limitations in the status/review docs. No live latency or cost improvement claim until measured on equivalent inputs.

## Execution and Handoff

Execution completed **natively** in this session after user approval. One read-only
reviewer checked the changes and verified the fixes to persistence cancellation,
circuit recovery and deletion ordering. Final validation: 253 offline tests passed,
2 optional renderer skips, 7 live deselected; Ruff passed. Deferred evidence/context
and infrastructure work remain outside this completed increment.

Keep commits limited to the work performed here if committing is requested.
Do not push, merge, deploy, change credentials or enable external writes.

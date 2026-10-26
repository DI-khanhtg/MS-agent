# Repair document reads after filename redaction

The latest stored turn inventories a local PDF, then calls `read_local_document`
with `[REDACTED]` in its path. That file does not exist. Direct extraction of the
actual user-selected PDF succeeds (6 pages, 35302 characters). The previous
OneDrive `/content` fix does not address local inventory identifiers.

1. Reproduce the inventory -> egress -> read failure with a numeric filename.
2. Add stable opaque document IDs derived from account root and relative path.
   IDs contain letters only so numeric PII redaction cannot corrupt them.
3. Resolve IDs strictly inside the current account's bounded document inventory.
   Keep relative-path compatibility and all existing path/link/size guards.
4. Return safe local read errors; instruct the agent to use document IDs and
   avoid inferring OCR problems from missing paths.
5. Verify owner isolation, restarts, chunk offsets, real framework tool results,
   the actual PDF locally without exposing its content, and the full test suite.

Review found that scoped listings can issue IDs beyond the capped root scan. A bounded,
owner-root keyed cache retains issued mappings and all read guards are reapplied. IDs
remain deterministic; scoped entries beyond the root cap must be relisted after restart
or cache eviction. Normal inventories within the root cap resolve across tool recreation.
DemoRuntime also consumes structured inventory/read errors and uses document IDs.

## Validation

- Failing numeric-name/egress regression reproduced before implementation.
- Actual user PDF read through the repaired local tools and egress gate: 6 pages,
  35302 source characters, all 11 chunks; content not printed or sent to a model.
- Owner isolation, tool recreation, chunk continuation, deleted/invalid references,
  masked-path recovery, scoped inventory caps and real framework tool delivery pass.
- Independent reviewer confirmed the bounded cache and account/path guards; all
  reported issues were addressed.
- `uv run --with playwright pytest -q --tb=short`: 414 passed, 9 skipped,
  one upstream Starlette/AnyIO deprecation warning.
- Ruff, edited-file formatting, lock consistency and diff whitespace checks pass.
- Running localhost health endpoint reports `ok`.

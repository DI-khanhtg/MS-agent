# Security model - Phase 4 through 10

## Trust boundaries

The API accepts only a Microsoft Entra access token intended for this backend. It validates
the signature, issuer, audience, expiry, tenant, delegated scope, user object ID, and—when
configured—the calling client ID. Request body fields are never used as identity.

The verified incoming access token is used only as the assertion in an OAuth on-behalf-of
exchange. The resulting Work IQ or Fabric token is passed as a bearer token to its remote MCP
endpoint and is never returned to the model or API caller.

Work IQ calls execute under the signed-in user's Microsoft 365 permissions. The application
also enforces a smaller read-only surface before the request reaches Work IQ.

## Isolation

Public session IDs are namespaced internally as:

```text
tenant ID : user object ID : public session ID
```

This prevents a user in the same or another tenant from attaching to another user's
conversation merely by supplying the same session ID. Work IQ authorization remains
delegated per call rather than being stored in a shared session.

Artifact files use the same verified tenant/user boundary. Tenant and object IDs are hashed
into fixed storage keys, artifact IDs must be UUIDs, filenames are server-generated, and the
download endpoint performs owner and SHA-256 integrity checks. Template paths come only from
the server-owned allowlist in `config/artifacts.yaml`.

## Egress gate

`config/egress.yaml` controls:

- prohibited secret and credential classifications;
- email/phone redaction;
- maximum collection length, string length, nesting depth, and serialized payload size;
- safe audit metadata.

The gate runs on user prompts and on every Work IQ and Fabric result before either is provided
to DeepSeek. A blocked payload results in an API error/tool error; the application does not
fall back to sending the original data.

## Controlled actions

Action payloads use strict per-type schemas and server-owned allowlists. Drafting and
requesting approval never performs a Microsoft 365 mutation. Only the authenticated approval
endpoint can move a pending action to execution. The store checks tenant/user ownership,
expiry, and state under one lock, so repeated approval fails closed. Logs contain action ID,
type, status, and a hashed subject—not message bodies or recipients.

Delete is excluded from action types, tool registration, Work IQ write policy, and the
approval configuration. The current in-memory store is a pilot-only boundary: run one worker
and do not treat it as durable audit evidence.

## Production checklist

- Set `AUTH_ENABLED=true`; never deploy company-data tools with anonymous API access.
- Use explicit tenant, audience, scope, and allowed-client values.
- Store secrets in a managed secret store, not a container image or source control.
- Restrict outbound network access to Entra, Work IQ, Fabric API, and DeepSeek endpoints.
- Review PII patterns and prohibited classifications with the organization's compliance
  owner before pilot use.
- Treat egress-policy changes as security-sensitive reviewed changes.
- Rotate the Entra client secret and migrate to certificate/workload identity when the
  deployment implementation supports it.
- Do not log access tokens, authorization headers, raw prompts, or raw Work IQ responses.
- Keep Fabric RLS/CLS and underlying source read permissions in place; do not broaden source
  access to compensate for failed queries.
- Replace the Phase 10 in-memory approval store with transactional durable storage and
  cross-worker idempotency before production.
- Put `data/artifacts` on encrypted durable storage with retention and deletion policies before
  production; the Phase 7 local store is intentionally not a records-management system.

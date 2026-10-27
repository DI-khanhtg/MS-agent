# Shared backend with Microsoft sign-in

The user approved a centrally hosted service: the owner configures Gemini/Entra once;
end users open the service and sign in with a work/school account. Multiple organizations
are supported through a multitenant Entra registration and delegated OBO. No deployment,
cloud resources or real tenant permissions will be changed in this implementation.

## Contract and tasks

1. Add `DEPLOYMENT_MODE=local|shared` (local compatibility),
   `AUTH_TENANT_MODE=allowlist|organizations`, `PUBLIC_BASE_URL`,
   `DOCUMENT_UPLOADS_ENABLED` (off by default), and bounded shared request/chat limits.
   `Settings.tenant_is_allowed(tid)` is the common policy; organizations accepts canonical
   work/school UUIDs, optional explicit AUTH_ALLOWED_TENANT_IDS narrows access, personal
   accounts and malformed/path-like tenants are rejected.
2. Validate real tenant-specific JWT issuer/signature/audience/scopes/authorized SPA.
   Use organizations only for browser authority; OBO always uses the verified tenant.
   Keep tenant caches bounded; discovery/JWKS must not fetch arbitrary domains.
3. Fail closed on insecure shared startup, require durable auth-backed state and explicit
   public HTTPS origin/callback. Optional owner-scoped uploads can run in shared production;
   development-only defaults remain unchanged. Keep one worker/replica for existing SDK
   session/file coordination; this is a shared-service starter, not distributed HA.
4. Shared UI exposes sign-in/account and privacy context, not token/Entra setup inputs.
   API responses are not cached; same-origin writes use the configured public origin.
   Limit aggregate API/chat requests and concurrent chats in addition to user quotas.
5. Add owner setup profile `setup --shared [--output .env.shared]`, public template,
   shared Compose/deployment guidance and user-first READMEs. Secrets remain server-only.
6. Test multi-tenant signed tokens, verified-tenant OBO, same-object-ID tenant separation,
   protected APIs/uploads/history/artifacts, setup profiles and shared login UI. Run full
   regression checks and scoped review before publishing to the existing feature branch.

Public HTTPS hosting, DNS, multitenant Entra registrations and tenant admin consent remain
owner/operator actions after code delivery. Never imply live deployment has occurred.

## Completed validation (2026-10-27)

- Signed token tests cover independent organizations, tenant-specific issuer/signature,
  audience/client/scope failures, personal/malformed tenant rejection, optional tenant
  narrowing, pinned Microsoft signing metadata and bounded tenant caches.
- Shared production integration proves that identical object IDs in different tenants
  cannot share chat history, uploaded files or artifacts; startup/auth/origin/rate/turn
  admission guards are covered. Shared login UI hides owner setup/manual-token controls.
- Independent review identified a disconnect admission leak. Streaming response cleanup
  now closes nested generators in the streaming task, shielded from disconnect cancellation.
  Five regressions cover ASGI 2.4 send errors/cancellation and ASGI 2.3 ContextVar cleanup.
- Windows SQLite writes measured 94-172 ms exposed old tests applying a 100 ms deadline
  to unrelated recovery/deletion turns. The forced first timeout remains 100 ms; only
  subsequent ordering checks receive a normal budget. Production timeouts are unchanged.
- `uv run --with playwright pytest -q --tb=short`: 494 passed, 10 skipped, one upstream
  Starlette/AnyIO deprecation warning. Skips require live providers, optional artifact
  runtime, or POSIX behavior unavailable on this Windows host.
- Supplied shared Compose/Caddy setup keeps backend internal and prepares HTTPS/SSE with
  persistent backend/certificate volumes. Actual Docker startup/TLS provisioning, cloud
  deployment and cross-organization consent have not been exercised here.
- Existing local settings remain supported; private operator env files are ignored and
  never included in public templates or end-user bootstrap responses.

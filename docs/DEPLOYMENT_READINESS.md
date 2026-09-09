# Hosting / deployment readiness — Mission Control

Preparation only. **Nothing has been deployed, committed, tagged, or changed in
`Agentic_os`.** This records what the system is today, what blocks exposing it
beyond loopback, and the options — so the hosting decision can be made
deliberately.

Audited at commit `b679ee2` (working tree, Phase 22 validated: 780 tests OK).

---

## Verdict

The application is **not ready to be exposed on a network today**, and the
reason is not packaging — it is authentication. One blocker is severe enough to
gate everything else.

The good news is that packaging is unusually easy: the codebase has **zero
third-party Python dependencies**, so there is no dependency resolution, no
lockfile, and no native build step to solve.

---

## Blockers, in priority order

### B1 — Every read endpoint is completely unauthenticated (severe)

`do_GET` performs an origin check and then serves data. There is no token check
on any read path. Only *mutating* `POST` actions call `_verify_auth`.

Probed directly against a loopback instance with **no `Authorization` header**:

```
200 /api/accounts        {"accounts": [{"id": "antigravity-account-1", ...
200 /api/overview        {"status": "RUNNING", "host": {"os": "CachyOS Linux", "cpu_cores": 2, "memory_gb": 16, ...
200 /api/usage           {"requests": 5, "successful_requests": 5, ...
200 /api/cost            {"summary": {"requests": 5, ...
200 /api/router/history  {"history": [{"timestamp": "2026-09-09T05:36:07...", "task_text": ...
200 /api/steering        {"steering": [{"path": "/home/setoo/YashDevops/Agentic...
```

Exposed beyond loopback, an unauthenticated caller obtains the full account
inventory and provider topology, host OS and hardware details, token usage and
cost history, the routing decision log including task text, local filesystem
paths, and a live event stream via `/api/events/stream`.

On loopback this is a defensible single-user design. It is not defensible on a
network. **This must be fixed before any hosting option is chosen**, because
every other option below assumes it.

Fix shape: require the same Bearer token on `do_GET` that `do_POST` already
enforces, with a deliberate allow-list for genuinely public paths (a health
endpoint, and `/static/` if the UI needs it pre-auth). The token mechanism
already exists and needs no redesign.

### B2 — The listener is hardcoded to loopback

```python
server = ThreadedHTTPServer(("127.0.0.1", port), MissionControlHandler)
```

The port is configurable (`BRAIN_PORT`, default `3333`); the interface is not.
Nothing outside the machine can reach it. This is currently acting as the only
thing preventing B1 from being an active exposure — so B2 must not be relaxed
before B1 is fixed.

### B3 — `http.server` is not a production server

`ThreadedHTTPServer` is the stdlib development server: no TLS, no request size
limits, no timeouts worth relying on, thread-per-connection, single process. It
should sit behind a reverse proxy that terminates TLS and enforces limits, or be
replaced. Note `/api/events/stream` is long-lived SSE, so the proxy needs
buffering disabled and a long read timeout.

### B4 — CORS allows only loopback origins, and a missing origin passes

`is_allowed_origin` returns `True` when `Origin` is absent (so non-browser
clients like `curl` are unaffected) and otherwise permits only `127.0.0.1`,
`localhost`, `::1`. A browser loading the UI from a real hostname would be
refused. Hosting with a browser UI requires adding that hostname explicitly —
not widening the check.

### B5 — The credential backend needs a session bus that containers lack

`CredentialManager` prefers `KeyringCredentialStore`, which shells out to
`secret-tool` (libsecret) and requires an active D-Bus session. In a container
there is none, so it silently falls back to `EncryptedFileStore`.

That fallback is functional but changes the security model: the encryption key
and its lifecycle become the operator's problem, and the store must live on a
persistent, restricted volume. This needs an explicit decision rather than a
default.

### B6 — CLI agent providers cannot work in a generic container

This is the constraint that shapes the whole hosting choice. `Path.home()`
assumptions appear in `agents/antigravity/auth.py`, `agents/antigravity/adapter.py`,
`agents/cline/auth.py`, `providers/registry/account_removal.py`,
`providers/registry/credential_manager.py`, and `providers/mcp/tool_catalog.py`.

Concretely, the agent providers require the `agy` and `cline` binaries on `PATH`
and isolated profiles under `~/.gemini` and `~/.mission-control/cline`. Six of
the eight registered accounts are CLI-agent accounts. Move the process into a
container without those binaries and profiles and those six accounts stop being
usable — the dashboard would host an orchestrator with nothing to orchestrate.

---

## Operational gaps (not blockers, but they will bite)

| Gap | Detail |
|---|---|
| Unbounded log growth | `runtime/` is 46 MB already: `routing_history.jsonl` 23 MB, `events.jsonl` 8.6 MB, `audit.jsonl` 2.7 MB. Append-only with no rotation. Needs rotation or retention, and the audit ledger needs a deliberate retention policy since it is the compliance record. |
| State must persist | `runtime/` holds the ledgers plus `mission_control.token` (mode `0600`). A container needs a writable volume; losing it loses the audit trail. |
| Build artifacts in tree | `runtime/sandboxes/` is 3.9 MB across 8 directories of swarm worktree copies. These must be excluded from any image; they also contain duplicate copies of source that would confuse scanning. |
| Rate limiter is per-process | In-memory sliding window, 30 requests / 60 s. Replicas would each get their own budget, so it is not a shared control. |
| Single process assumed | In-memory event bus, wizard sessions, and rate limiter are process-local. Horizontal scaling would split state; plan for exactly one replica unless that is redesigned. |
| Python version | Running 3.14.7. No `match` statements, but modern typing throughout. Pin the base image explicitly rather than tracking `python:3`. |
| No health endpoint for probes | There is `/api/health`, but it is not documented as a probe target and currently sits behind no auth. Decide whether it stays public for liveness. |

---

## Hosting options

Given B6, the realistic choices are genuinely different in kind, not just in
provider.

### Option 1 — Stay local, publish through Cloudflare Tunnel (recommended)

Keep the process on the workstation where the CLI agents, their binaries, and
their profiles already live. Publish it with a Cloudflare Tunnel fronted by
Access, so there is no inbound port and no public listener.

* Preserves all 8 accounts and the whole orchestration capability.
* B2 can stay as-is — the tunnel connects to loopback.
* B3 and B4 are largely handled by the tunnel and Access.
* B1 must still be fixed: Access is perimeter auth, and the app should not rely
  solely on a perimeter.
* Cost: negligible. Availability: bounded by the workstation being on.

### Option 2 — Containerise the dashboard as read-only observability

Ship the HTTP surface without the CLI agent providers, accepting that those
accounts report unavailable. Useful only if the goal is remote *visibility*.

* Requires B1–B5 all resolved.
* Honest framing: this hosts a viewer, not the orchestrator.

### Option 3 — Full AKS deployment

Technically possible for the HTTP surface, but it does not solve B6. Antigravity
is GUI/OAuth-bound to a desktop session and cannot be reproduced in a cluster
pod. This would mean re-architecting agent execution — a project, not a
deployment.

Existing infrastructure is available if this path is chosen (DEV
`dev-agentic-os-aks-01`, UAT `aks-agenticos-uat`, PROD `aks-agenticos-prod`, per
`.kiro/steering/azure-env-map.md`), but I would not recommend starting here.

---

## Suggested sequence, if you approve

Ordered so that nothing increases exposure before the thing that makes exposure
safe is in place.

1. **Fix B1.** Require the Bearer token on `do_GET`, with an explicit public
   allow-list. Add tests asserting each sensitive read path returns 401
   unauthenticated — mirroring the pattern already used for the wizard's ten
   step actions in `test_phase22_demo_verification.py`.
2. **Decide the option.** This determines whether B2, B5, and B6 need solving
   at all. Option 1 avoids most of them.
3. **Add log rotation and an audit retention policy** before the ledgers grow
   further.
4. **Make the bind interface configurable** (`BRAIN_HOST`, defaulting to
   `127.0.0.1` so the safe default is unchanged) — only after step 1.
5. **Package**, if containerising: pin `python:3.14-slim`, no
   `requirements.txt` needed, `.dockerignore` excluding `.git`,
   `runtime/sandboxes`, `runtime/cache`, `runtime/temporary`, and `__pycache__`.
6. **Add the hosted origin** to `is_allowed_origin` if a browser UI is served
   from a real hostname.
7. **Re-run the full gate** (780 tests) plus the non-interference check after
   each change.

Two open questions I cannot decide for you: whether hosted Mission Control needs
to *drive* agents or only *observe* them (this settles Option 1 vs 2), and who
should be able to reach it (single operator vs a team, which settles whether the
single shared Bearer token is sufficient or identity-based access is needed).

---

## Related open finding

`docs/PHASE22_ACCOUNT_AUTHENTICATION.md` records an unresolved defect worth
settling before hosting: `openai-generic-1` declares
`capabilities: ['chat', 'streaming']`, neither of which is a `Capability` enum
member, so the only direct-API account in the registry is silently unroutable.
It matters here because a hosted deployment that cannot run CLI agents would
depend on exactly that class of account.

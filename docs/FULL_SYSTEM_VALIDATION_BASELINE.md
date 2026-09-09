# Full System Validation — Safety Baseline (Part 0)

Captured **before any validation activity** on 2026-09-08, ~17:50 local (Asia/Kolkata), host `CachyOS (2 cores / 16 GB)`, Python 3.14.7.

Purpose: prove after testing that the user's live environment (Antigravity GUI, `~/.gemini`, `service=gemini` keyring, `Agentic_os` repo) was **not disturbed**.

## 1. Antigravity GUI processes (live, PS-verified)

| Role | PID | Start time (ps lstart) | Command (truncated, safe) |
|---|---|---|---|
| AGY hub (Gemini CLI hub) | **4904** | Tue Sep 8 12:07:43 2026 | `/home/setoo/.gemini/bin/agy --hub --hub-port=43591 --app_data_dir=antigravity --add-dir=/home/setoo/YashDevops/Agentic_os` |
| Antigravity IDE (GUI main) | **5854** | Tue Sep 8 12:11:16 2026 | `/opt/antigravity-ide/antigravity-ide` |
| IDE zygote / helpers | 5857, 5858, 5860, 5895, 5933 | Sep 8 12:11:16–18 2026 | `/opt/antigravity-ide/antigravity-ide --type=zygote ...` |
| Language server | 5943 | Sep 8 12:11:18 2026 | `.../language_server_linux_x64 ...` (csrf/port args redacted) |
| crashpad | 5877 | Sep 8 12:11:17 2026 | `chrome_crashpad_handler ...` |

Primary monitored identities: **PID 4904 (agy hub)** and **PID 5854 (IDE)**. Validation must never signal, restart, or otherwise disturb these.

## 2. `~/.gemini` directory fingerprints (mtime epoch, top level)

| Path | mtime epoch | ISO |
|---|---|---|
| `/home/setoo/.gemini` | 1788782284 | 2026-09-07 17:28:04 +0530 |
| `antigravity` | 1788861582 | 2026-09-08 15:29:42 |
| `antigravity-account-3` | 1788861345 | 2026-09-08 15:25:45 |
| `antigravity-account-jadhav` | 1788861338 | 2026-09-08 15:25:38 |
| `antigravity-account-yash` | 1788861356 | 2026-09-08 15:25:56 |
| `antigravity-api` | 1788757889 | 2026-09-07 10:41:29 |
| `app_data_dir` values used by Mission Control profiles | — | (see §6) |
| `antigravity-cli` | 1788850355 | 2026-09-08 12:22:35 |
| `antigravity-ide` | 1788849668 | 2026-09-08 12:11:08 |
| `antigravity-ide-3` | 1788776877 | 2026-09-07 15:57:57 |
| `bin` | 1788861359 | 2026-09-08 15:25:59 |
| `config` | 1788508740 | 2026-09-07 15:59:00 |
| `home` | 1788776940 | : 2026-09-07 15:59:00 |

## 3. Keyring fingerprints

| File | Size (bytes) | mtime epoch | sha256 prefix |
|---|---|---|---|
| `~/.local/share/keyrings/login.keyring` | 5160 | 1788862691 (2026-09-08 16:08:11 +0530) | `f2926bd1e94555b9` |
| `~/.local/share/keyrings/user.keystore` | 207 | 1787564355 | — |

Keyring namespaces in use by Mission Control: `service=mission-control` (own) — `service=gemini` must remain untouched. `secretstorage` is not directly importable in Python 3.14 env; fingerprint = file size+mtime+hash prefix above.

## 4. Target repo `~/YashDevops/Agentic_shared_memory` git state

- Branch: **main**; HEAD `b679ee2` "docs: add Phase 5 Independent Production Validation Report"
- **160 uncommitted working-tree changes (modified + untracked) pre-existing at validation start** — MUST remain preserved; **no automatic commits**.
- Git stash list: empty.
- 78 test files under `tests/` (unit + integration + e2e).
- No `requirements.txt` / `pyproject.toml` (stdlib-only project).

## 5. `~/YashDevops/Agentic_os` state (metadata only)

- Not a git repository at the top level (polyrepo: contains subrepos `agentic-os-comms-engine`, `agentic-os-infra`, etc.).
- Top-level entries fingerprinted via mtimes (sample of latest): `reports` 1787571399, `ISMS` 1787725028, `scripts` 1788257108, `agentic-os-infra` 1788266586, `agentic-os-agent-workflow` 1788328027, `tests` 1788528176, plus dated `.bak` files and CSV audit exports. Must remain **zero mutations** during validation.

## 6. Mission Control runtime state (safe metadata)

- `runtime/` contains: `analytics/`, `audit/`, `cache/`, `jobs/`, `logs/`, `mcp/`, `plans/`, `sandboxes/`, `temporary/`, `mission_control.token` (mode 0600, **value never read/printed**).
- State dirs: `tasks/{queue,active,completed,failed}`, `sessions/session_registry.json`, `handoffs/current.{md,json}`, `memory/store/shared_memory.db`, `events` bus at `runtime/logs/events.jsonl`.
- Dashboard: `127.0.0.1:3333`, Bearer token from `runtime/mission_control.token`.

## 7. Existing test counts (Phase-21 documented baseline)

- Documented baseline (docs): **512 tests, 0 failures** (418 unit + 94 integration).
- README badge claims 510/510; README §20 text claims 175/175 (inconsistent docs — recorded as known issue, P3).

## 8. Non-interference criteria (Part 22 / 32)

| Check | Baseline value |
|---|---|
| agy hub PID 4904 alive, start 12:07:43 | must be unchanged |
| IDE PID 5854 alive, start 12:11:16 | must be unchanged |
| No process launched with an intention to signal PIDs 4904/5854 | enforced |
| `~/.gemini` top-level mtimes | unchanged vs §2 |
| `login.keyring` size 5160 / mtime 1788862691 / hash prefix f2926bd1... | unchanged |
| `Agentic_os` mtimes + no new files | unchanged |
| Target repo git status | 160 changes preserved, no new commit |

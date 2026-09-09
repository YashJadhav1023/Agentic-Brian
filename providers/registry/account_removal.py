"""Safe, idempotent account removal for Mission Control (Phase 22, Part 13).

Removing an account must be surgical and reversible only in the sense that it
touches nothing beyond the single account it was asked to remove:

* The account is transitioned to ``DISABLED`` first, so the router immediately
  stops admitting it as a candidate.
* In-flight work is drained (or refused), never killed.
* Only that account's credential reference is deleted from ``CredentialManager``.
* Only that account's isolated runtime and profile directories are removed.
* Every other account, and above all the running Antigravity IDE GUI profile
  ``~/.gemini/antigravity-ide``, is left completely untouched.

A hard guard (:class:`ProtectedProfileError`) refuses to remove any profile that
is protected or owned by the GUI, raising a clear error instead of deleting it.
The whole operation is idempotent: removing an already-removed account is a
no-op success, and it emits ``ACCOUNT_REMOVE`` audit entries throughout.

Standard library only.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Profiles that may NEVER be deleted or modified by account removal. These are
#: owned by the live Antigravity IDE GUI (keyring service=gemini, PID-backed).
PROTECTED_PROFILE_NAMES: frozenset[str] = frozenset({"antigravity-ide", "ide"})

#: Roots that isolated account runtime / profile directories legitimately live
#: under. Removal will only ever delete a directory that resolves *inside* one
#: of these roots, so a malformed path can never escape into the wider system.
_GEMINI_ROOT = (Path.home() / ".gemini").resolve()
_CLINE_ROOT = (Path.home() / ".mission-control" / "cline").resolve()
_ALLOWED_RUNTIME_ROOTS: tuple[Path, ...] = (_GEMINI_ROOT, _CLINE_ROOT)

#: Accounts frozen against removal for backward compatibility with Phase 8.
FROZEN_ACCOUNT_IDS: frozenset[str] = frozenset(
    {"antigravity-account-1", "antigravity-account-2", "antigravity-account-3"}
)


class ProtectedProfileError(RuntimeError):
    """Raised when removal would touch a protected or GUI-owned profile."""


def _protected_profile_targets() -> set[Path]:
    targets: set[Path] = set()
    for name in PROTECTED_PROFILE_NAMES:
        targets.add((_GEMINI_ROOT / name).resolve())
    return targets


def assert_removable_profile(path: Path) -> Path:
    """Validate that ``path`` may be safely removed, else raise.

    Guarantees:
    * the path is not a protected/GUI-owned profile,
    * the path resolves strictly inside one of the allowed runtime roots,
    * the path is not one of the runtime roots themselves.
    """
    resolved = Path(path).expanduser().resolve()
    protected = _protected_profile_targets()

    if resolved.name in PROTECTED_PROFILE_NAMES or resolved in protected:
        raise ProtectedProfileError(
            f"Refusing to remove protected/GUI-owned profile: {resolved}"
        )
    # Never allow removal of a path that is an ancestor of a protected profile.
    for prot in protected:
        if resolved in prot.parents:
            raise ProtectedProfileError(
                f"Refusing to remove '{resolved}' because it contains protected profile '{prot}'"
            )

    inside_allowed = False
    for root in _ALLOWED_RUNTIME_ROOTS:
        if resolved == root:
            raise ProtectedProfileError(
                f"Refusing to remove runtime root itself: {resolved}"
            )
        try:
            resolved.relative_to(root)
            inside_allowed = True
            break
        except ValueError:
            continue
    if not inside_allowed:
        raise ProtectedProfileError(
            f"Refusing to remove '{resolved}': not inside an allowed runtime root"
        )
    return resolved


def _candidate_profile_dirs(account: Any) -> list[Path]:
    """Best-effort enumeration of the isolated dirs owned by one account.

    Reads only from the account's own metadata / attributes; it never guesses
    a directory belonging to another account.
    """
    dirs: list[Path] = []
    metadata = getattr(account, "metadata", {}) or {}

    def _add(value: Any) -> None:
        if not value:
            return
        try:
            dirs.append(Path(str(value)).expanduser())
        except Exception:
            pass

    # Antigravity isolated profile under ~/.gemini/<app_data_dir>.
    app_data_dir = metadata.get("app_data_dir") or metadata.get("data_dir")
    if app_data_dir:
        # If it is a bare profile name, resolve it under the gemini root.
        p = Path(str(app_data_dir)).expanduser()
        if not p.is_absolute():
            p = _GEMINI_ROOT / str(app_data_dir)
        dirs.append(p)

    # Explicit absolute directories recorded on the account metadata.
    _add(metadata.get("profile_dir"))
    _add(metadata.get("config_dir"))
    _add(metadata.get("runtime_dir"))

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


@dataclass
class RemovalResult:
    """Structured, secret-free outcome of a safe account removal."""

    account_id: str
    provider_id: str = ""
    removed: bool = False
    already_absent: bool = False
    credential_deleted: bool = False
    directories_removed: list[str] = field(default_factory=list)
    directories_skipped: list[str] = field(default_factory=list)
    drained_inflight: int = 0
    correlation_id: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "provider_id": self.provider_id,
            "removed": self.removed,
            "already_absent": self.already_absent,
            "credential_deleted": self.credential_deleted,
            "directories_removed": self.directories_removed,
            "directories_skipped": self.directories_skipped,
            "drained_inflight": self.drained_inflight,
            "correlation_id": self.correlation_id,
            "error": self.error,
        }

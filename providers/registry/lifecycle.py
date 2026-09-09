"""Universal Account Lifecycle State Machine for Mission Control.

Implements the formal 14-state lifecycle model for Phase 22, decoupling:
- Account lifecycle progression (DISCOVERED -> CONFIGURING -> AUTHENTICATING -> AUTHENTICATED -> VALIDATING -> READY -> ONLINE / OFFLINE / DISABLED)
- Failure and error states (AUTH_FAILED, AUTH_CANCELLED, VALIDATION_FAILED, PROVIDER_UNAVAILABLE, CONFIG_ERROR)
- Authentication state (AuthState)
- Runtime process state (ProcessState)
- Operational health state (HealthState)
- Task execution state (TaskExecutionState)

All transitions are deterministic, validated, auditable, and safe.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AccountLifecycleState(str, Enum):
    """Universal 14-state account lifecycle machine."""
    # Progressive onboarding states
    DISCOVERED = "DISCOVERED"
    CONFIGURING = "CONFIGURING"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    VALIDATING = "VALIDATING"
    READY = "READY"

    # Operational runtime states
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"

    # Failure states
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_CANCELLED = "AUTH_CANCELLED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CONFIG_ERROR = "CONFIG_ERROR"


class AuthState(str, Enum):
    """Orthogonal authentication state of an account."""
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_CANCELLED = "AUTH_CANCELLED"
    EXPIRED = "EXPIRED"


class HealthState(str, Enum):
    """Orthogonal operational health state of an account."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class ProcessState(str, Enum):
    """Orthogonal process/execution state of an agent runtime."""
    IDLE = "IDLE"
    WORKING = "WORKING"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"
    UNKNOWN = "UNKNOWN"


class TaskExecutionState(str, Enum):
    """Orthogonal task execution state for an account."""
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InvalidLifecycleTransitionError(ValueError):
    """Raised when an illegal or unsupported lifecycle state transition is requested."""
    def __init__(self, from_state: AccountLifecycleState, to_state: AccountLifecycleState, reason: str = "") -> None:
        msg = f"Invalid account lifecycle transition from '{from_state.value}' to '{to_state.value}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason


# Directed graph of permissible state transitions
VALID_TRANSITIONS: dict[AccountLifecycleState, set[AccountLifecycleState]] = {
    AccountLifecycleState.DISCOVERED: {
        AccountLifecycleState.DISCOVERED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.CONFIGURING: {
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.CONFIG_ERROR,
        AccountLifecycleState.DISCOVERED,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.AUTHENTICATING: {
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.AUTHENTICATED,
        AccountLifecycleState.AUTH_FAILED,
        AccountLifecycleState.AUTH_CANCELLED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.AUTHENTICATED: {
        AccountLifecycleState.AUTHENTICATED,
        AccountLifecycleState.VALIDATING,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.VALIDATING: {
        AccountLifecycleState.VALIDATING,
        AccountLifecycleState.READY,
        AccountLifecycleState.VALIDATION_FAILED,
        AccountLifecycleState.PROVIDER_UNAVAILABLE,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.READY: {
        AccountLifecycleState.READY,
        AccountLifecycleState.ONLINE,
        AccountLifecycleState.OFFLINE,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.PROVIDER_UNAVAILABLE,
    },
    AccountLifecycleState.ONLINE: {
        AccountLifecycleState.ONLINE,
        AccountLifecycleState.OFFLINE,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.PROVIDER_UNAVAILABLE,
    },
    AccountLifecycleState.OFFLINE: {
        AccountLifecycleState.OFFLINE,
        AccountLifecycleState.ONLINE,
        AccountLifecycleState.READY,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.PROVIDER_UNAVAILABLE,
    },
    AccountLifecycleState.DISABLED: {
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.ONLINE,
        AccountLifecycleState.OFFLINE,
        AccountLifecycleState.READY,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.DISCOVERED,
    },
    AccountLifecycleState.AUTH_FAILED: {
        AccountLifecycleState.AUTH_FAILED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.OFFLINE,
    },
    AccountLifecycleState.AUTH_CANCELLED: {
        AccountLifecycleState.AUTH_CANCELLED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.AUTHENTICATING,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.DISCOVERED,
    },
    AccountLifecycleState.VALIDATION_FAILED: {
        AccountLifecycleState.VALIDATION_FAILED,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.VALIDATING,
        AccountLifecycleState.DISABLED,
        AccountLifecycleState.OFFLINE,
    },
    AccountLifecycleState.PROVIDER_UNAVAILABLE: {
        AccountLifecycleState.PROVIDER_UNAVAILABLE,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.VALIDATING,
        AccountLifecycleState.READY,
        AccountLifecycleState.OFFLINE,
        AccountLifecycleState.ONLINE,
        AccountLifecycleState.DISABLED,
    },
    AccountLifecycleState.CONFIG_ERROR: {
        AccountLifecycleState.CONFIG_ERROR,
        AccountLifecycleState.CONFIGURING,
        AccountLifecycleState.DISABLED,
    },
}


class AccountLifecycleStateMachine:
    """Validator and driver for account lifecycle state changes."""

    @classmethod
    def can_transition(
        cls,
        from_state: AccountLifecycleState | str,
        to_state: AccountLifecycleState | str,
    ) -> bool:
        """Check if transition between two states is valid."""
        try:
            f = AccountLifecycleState(from_state)
            t = AccountLifecycleState(to_state)
        except ValueError:
            return False
        return t in VALID_TRANSITIONS.get(f, set())

    @classmethod
    def validate_transition(
        cls,
        from_state: AccountLifecycleState | str,
        to_state: AccountLifecycleState | str,
        reason: str = "",
    ) -> None:
        """Validate transition, raising InvalidLifecycleTransitionError if invalid."""
        f = AccountLifecycleState(from_state)
        t = AccountLifecycleState(to_state)
        if t not in VALID_TRANSITIONS.get(f, set()):
            raise InvalidLifecycleTransitionError(f, t, reason)

    @classmethod
    def transition(
        cls,
        account: Any,
        to_state: AccountLifecycleState | str,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AccountLifecycleState:
        """Execute a state transition on an account instance."""
        target = AccountLifecycleState(to_state)
        current = getattr(account, "lifecycle_state", AccountLifecycleState.DISCOVERED)
        if not isinstance(current, AccountLifecycleState):
            try:
                current = AccountLifecycleState(current)
            except ValueError:
                current = AccountLifecycleState.DISCOVERED

        cls.validate_transition(current, target, reason=reason or "")

        # Apply state changes to account
        account.lifecycle_state = target
        account.updated_at = datetime.now(timezone.utc).isoformat()
        if reason:
            account.health_reason = reason

        # Map to orthogonal states automatically where appropriate
        if target == AccountLifecycleState.ONLINE:
            account.auth_state = AuthState.AUTHENTICATED
            account.health_state = HealthState.HEALTHY
            account.enabled = True
        elif target == AccountLifecycleState.OFFLINE:
            account.health_state = HealthState.UNHEALTHY
        elif target == AccountLifecycleState.DISABLED:
            account.enabled = False
        elif target == AccountLifecycleState.AUTHENTICATED:
            account.auth_state = AuthState.AUTHENTICATED
        elif target == AccountLifecycleState.AUTH_FAILED:
            account.auth_state = AuthState.AUTH_FAILED
            account.health_state = HealthState.UNHEALTHY
        elif target == AccountLifecycleState.AUTH_CANCELLED:
            account.auth_state = AuthState.AUTH_CANCELLED
        elif target == AccountLifecycleState.VALIDATION_FAILED:
            account.health_state = HealthState.UNHEALTHY
        elif target == AccountLifecycleState.PROVIDER_UNAVAILABLE:
            account.health_state = HealthState.UNHEALTHY
        elif target == AccountLifecycleState.CONFIG_ERROR:
            account.health_state = HealthState.UNHEALTHY

        # Synchronize backward-compatible AccountStatus
        cls.sync_status_field(account)

        return target

    @classmethod
    def sync_status_field(cls, account: Any) -> None:
        """Keep account.status in sync with lifecycle_state for backward compatibility."""
        from providers.registry.account_registry import AccountStatus

        ls = getattr(account, "lifecycle_state", AccountLifecycleState.DISCOVERED)
        if ls == AccountLifecycleState.ONLINE:
            account.status = AccountStatus.ONLINE
        elif ls == AccountLifecycleState.OFFLINE:
            account.status = AccountStatus.OFFLINE
        elif ls == AccountLifecycleState.DISABLED:
            account.status = AccountStatus.DISABLED
        elif ls == AccountLifecycleState.AUTH_FAILED:
            account.status = AccountStatus.AUTH_ERROR
        elif ls == AccountLifecycleState.AUTH_CANCELLED:
            account.status = AccountStatus.AUTH_ERROR
        elif ls == AccountLifecycleState.CONFIG_ERROR:
            account.status = AccountStatus.CONFIG_ERROR
        elif ls == AccountLifecycleState.READY:
            account.status = AccountStatus.ONLINE
        elif ls in (
            AccountLifecycleState.DISCOVERED,
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
        ):
            # During onboarding, map to UNKNOWN or NOT_CONFIGURED
            account.status = getattr(AccountStatus, "NOT_CONFIGURED", AccountStatus.UNKNOWN)
        elif ls == AccountLifecycleState.PROVIDER_UNAVAILABLE:
            account.status = AccountStatus.OFFLINE
        elif ls == AccountLifecycleState.VALIDATION_FAILED:
            account.status = AccountStatus.CONFIG_ERROR
        else:
            account.status = getattr(AccountStatus, "UNKNOWN", AccountStatus.NOT_CONFIGURED)

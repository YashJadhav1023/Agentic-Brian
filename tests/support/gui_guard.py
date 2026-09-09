"""Non-interference helpers for the running Antigravity IDE GUI (Phase 22, Part 15).

Why this module exists
----------------------
Earlier phases asserted non-interference by hardcoding a literal PID (``5854``)
into integration tests. That is unsound for two independent reasons:

1. **It fails on any legitimate restart.** The user is free to close and reopen
   the IDE. A test that demands one specific PID reports a *false* interference
   violation the moment they do, which trains readers to ignore the signal.
2. **PIDs are reused.** After the IDE restarted, PID 5854 was reassigned to a
   kernel worker thread (``kworker/u16:16-btrfs-endio``). A bare
   ``ps -p 5854`` check therefore *succeeded* while pointing at something that
   was never the GUI. Pinning a number can silently assert the wrong process.

The guarantee we actually need is: *an Antigravity IDE GUI is running and we did
not disturb it.* That is a property of the process **identity**, not of a
number, so these helpers discover the process by executable path and report its
PID and start time as observed evidence.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

#: Matches the installed IDE binary. Deliberately the executable path rather
#: than a bare name, so unrelated processes that merely mention "antigravity"
#: (a CLI invocation, a grep, an editor buffer) cannot satisfy the check.
GUI_PROCESS_PATTERN = "/opt/antigravity-ide/antigravity-ide"


@dataclass(frozen=True)
class GuiProcess:
    """An observed Antigravity IDE GUI process."""

    pid: int
    started: str
    command: str


def _resolves_to_gui_binary(pid: str) -> bool:
    """True only when the process's actual executable is the IDE binary.

    ``pgrep -f`` matches the whole command line, so any shell command that
    merely *mentions* the binary path — including a test harness printing it —
    would otherwise be counted as a running GUI. Reading ``/proc/<pid>/exe``
    resolves what the kernel actually loaded, which cannot be spoofed by an
    argument string.
    """
    try:
        return os.readlink(f"/proc/{pid}/exe") == GUI_PROCESS_PATTERN
    except (OSError, ValueError):
        return False


def find_gui_processes() -> list[GuiProcess]:
    """Return every running Antigravity IDE GUI process.

    Returns an empty list when the IDE is not running. Callers decide whether
    absence is a failure; it is not inherently a non-interference violation,
    because the user may simply have closed the editor.
    """
    proc = subprocess.run(
        ["pgrep", "-f", GUI_PROCESS_PATTERN],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    found: list[GuiProcess] = []
    for raw_pid in proc.stdout.split():
        if not raw_pid.isdigit():
            continue
        # Discard command-line-only matches such as a shell mentioning the path.
        if not _resolves_to_gui_binary(raw_pid):
            continue
        detail = subprocess.run(
            ["ps", "-o", "pid=,lstart=,args=", "-p", raw_pid],
            capture_output=True,
            text=True,
            check=False,
        )
        if detail.returncode != 0 or not detail.stdout.strip():
            # Raced with process exit between pgrep and ps; skip rather than fail.
            continue
        line = detail.stdout.strip()
        parts = line.split(maxsplit=6)
        if len(parts) < 7:
            continue
        pid_text, started, command = parts[0], " ".join(parts[1:6]), parts[6]
        if not pid_text.isdigit():
            continue
        found.append(GuiProcess(pid=int(pid_text), started=started, command=command))
    return found


def gui_is_running() -> bool:
    """True when at least one Antigravity IDE GUI process is present."""
    return bool(find_gui_processes())


def describe_gui() -> str:
    """Human-readable evidence line for the observed GUI, for assertion messages."""
    procs = find_gui_processes()
    if not procs:
        return "no Antigravity IDE GUI process found"
    return "; ".join(f"pid={p.pid} started={p.started}" for p in procs)

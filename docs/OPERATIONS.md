# Operations & Runbook

## Routine Operations

### Starting Mission Control Dashboard
```bash
python3 ui/dashboard/dashboard.py
```

### Checking System Health
```bash
python3 scripts/brain.py agents
```

### Reclaiming Stale Locks
Locks expire automatically after 10 minutes (600s TTL). To force reclaim:
```python
from locks.file_locker import FileLocker
FileLocker().recover_stale_locks()
```

### Crash Recovery
If the host reboots or an agent process crashes while tasks are active:
```python
from tasks.manager import TaskManager
TaskManager().recover_orphaned_tasks()
```

# Mission Control UI Guide

The Mission Control dashboard runs on port 3333 (`http://127.0.0.1:3333`) using pure vanilla HTML, Tailwind CSS, FontAwesome, and Marked.js with 100% offline vendored assets.

## Dashboard Sections

1. **Mission Overview**: Quick KPIs (Active Agents, Running Tasks, Completed Tasks, Shared Memories) and Quick Task Dispatch.
2. **Agents & Accounts**: Live status of all 4 active agents, displaying Account 1 and Account 2 separately with health status, models, and capabilities.
3. **Tasks (Kanban)**: Ready Queue, Active Tasks, and Completed Tasks with live task cards.
4. **Execution Flow**: Step-by-step visual pipeline of autonomous task delegation.
5. **Shared Memory**: Searchable memory explorer with scope badges and importance indicators.
6. **Handoffs**: Markdown viewer for active and archived handoff records.
7. **Live Events**: Real-time telemetry event stream.
8. **Git & Security**: Repository branch, latest commit hash, working tree status, and zero-leak audit badge.

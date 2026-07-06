---
description: Start the Too Many Papers web UI and return the link to open it.
---

Call the `webui_launch` MCP tool (from the `too-many-papers` MCP server). Do not ask for confirmation first — just call it. If the web UI is already running, this restarts it (kills the existing process and starts a fresh one) rather than leaving a possibly stale/stuck instance in place. Then report back exactly what the tool returns, including the URL (http://localhost:3737 by default), so the user can open it in their browser. If the tool reports an error (e.g. Node.js missing), relay that error message as-is.

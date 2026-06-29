# Too Many Papers

**An LLM-powered knowledge graph for the papers you'll never finish reading.**

A local-first research assistant. No cloud, no database, no subscriptions — just JSON files and an MCP server that lets Claude read and write them through validated tools.

## Components

| Component | What it does |
|-----------|--------------|
| **Skill** (`skills/too-many-papers`) | Behavioral rules, onboarding flow, anti-hallucination protocol, and the hardcoded morning-briefing prompt. Loads automatically when you talk about papers, research concepts, or ask for a briefing. |
| **MCP server** (`server/`) | 35 tools (`papers_*`, `venues_*`, `graph_*`, `citations_*`) backed by `papers_api.py`. All reads/writes to `_papers.json`, `_venues.json`, `_graph.json` go through here — the LLM cannot invent node/edge/interaction types or bypass validation. |
| **Paper Library web UI** (`webui/`) | Local browser app for searching, filtering, and pinning papers, with citation network links and a PDF viewer. Shares the same data files as the MCP server. |

## Setup

Requires **[uv](https://docs.astral.sh/uv/)**. The server runs via `uv run`, which provisions its own Python interpreter and installs dependencies (`mcp`) on first launch — nothing to `pip install` by hand, and no dependency on a system `python`/`python3` being on PATH.

- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

(Node.js 18+ is only needed if you want the web UI.)

Once installed via Cowork / Claude Code, the MCP server starts automatically — no manual `claude mcp add` step needed.

## Usage

Just talk about papers. On first use, the skill walks you through onboarding: 3 starter research concepts, optional active projects, and an optional daily briefing schedule.

Examples:
- "I just read this paper: [link] — add it to my library"
- "What should I read next on segmentation?"
- "Give me today's paper briefing"
- "Connect this paper to my FCD project"

To open the visual library, run:

```bash
python webui/launch.py
```

then visit http://localhost:3737. (Or double-click `webui/launch.bat` on Windows.)

## Data

Everything lives in `server/_papers.json`, `server/_venues.json`, `server/_graph.json`. Plain JSON, version-controllable, portable. Back up by copying the `server/` folder or committing it to git.

## License

MIT

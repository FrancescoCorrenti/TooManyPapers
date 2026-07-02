# Too Many Papers

**An LLM-powered knowledge graph for the papers you'll never finish reading.**

A local-first research assistant. No cloud, no database, no subscriptions — just JSON files and an MCP server that lets Claude read and write them through validated tools.

## Components

| Component | What it does |
|-----------|--------------|
| **Skill** (`skills/too-many-papers`) | Behavioral rules, onboarding flow, anti-hallucination protocol, and the hardcoded morning-briefing prompt. Loads automatically when you talk about papers, research concepts, or ask for a briefing. |
| **MCP server** (`server/`) | 39 tools (`papers_*`, `venues_*`, `graph_*`, `citations_*`) backed by `papers_api.py`. All reads/writes to `_papers.json`, `_venues.json`, `_graph.json` go through here — each tool's parameters are typed per node/edge type, so the LLM can't invent fields, node/edge/interaction types, or bypass validation. |
| **Too Many Papers web UI** (`webui/`) | Local browser app for searching, filtering, and pinning papers, with citation network links and a PDF viewer. Shares the same data files as the MCP server. |

## Setup

Requires **[uv](https://docs.astral.sh/uv/)**. The server runs via `uv run`, which provisions its own Python interpreter and installs dependencies (`mcp`) on first launch — nothing to `pip install` by hand, and no dependency on a system `python`/`python3` being on PATH.

- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

(Node.js 18+ is only needed if you want the web UI.)

Once installed via Cowork / Claude Code, the MCP server starts automatically — no manual `claude mcp add` step needed.

## Usage

Just talk about papers. On first use, the skill gives a brief self-introduction, then asks a single open question — describe what you're currently working on — and drafts a proposed set of concepts/projects from your answer for you to confirm or edit, followed by an optional daily briefing schedule.

Examples:
- "I just read this paper: [link] — add it to my library"
- "What should I read next on segmentation?"
- "Give me today's paper briefing"
- "Connect this paper to my FCD project"

To open the visual library, run `/too-many-papers:webui`, or just ask to open Too Many Papers. This calls the `webui_launch` MCP tool and gives you the link (http://localhost:3737) — no manual script to find or run.

## Data

Everything lives in `server/_papers.json`, `server/_venues.json`, `server/_graph.json`. Plain JSON, version-controllable, portable. Back up by copying the `server/` folder or committing it to git.

## License

MIT

# Too Many Papers

**An LLM-powered knowledge graph for the papers you'll never finish reading.**

A local-first research assistant. No cloud, no database, no subscriptions — just JSON files and an MCP server that lets Claude read and write them through validated tools.

## Components

| Component | What it does |
|-----------|--------------|
| **Skill** (`skills/too-many-papers`) | Behavioral rules, onboarding flow, anti-hallucination protocol, and the hardcoded morning-briefing prompt. Loads automatically when you talk about papers, research concepts, or ask for a briefing. |
| **MCP server** (`server/`) | 40 tools (`papers_*`, `venues_*`, `graph_*`, `citations_*`) backed by `papers_api.py`. All reads/writes to `_papers.json`, `_venues.json`, `_graph.json` go through here — each tool's parameters are typed per node/edge type, so the LLM can't invent fields, node/edge/interaction types, or bypass validation. `papers_discover` queries arXiv, Semantic Scholar, and OpenAlex directly, so paper discovery never depends on general web search. |
| **Too Many Papers web UI** (`webui/`) | Local browser app for searching, filtering, and pinning papers, with citation network links and a PDF viewer. Shares the same data files as the MCP server. |

## Setup

Requires **[uv](https://docs.astral.sh/uv/)**. The server runs via `uv run`, which provisions its own Python interpreter and installs dependencies (`mcp`) on first launch — nothing to `pip install` by hand, and no dependency on a system `python`/`python3` being on PATH.

- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

(Node.js 18+ is only needed if you want the web UI.)

Once installed via Cowork / Claude Code, the MCP server starts automatically — no manual `claude mcp add` step needed.

### Optional environment variables (paper discovery & citations)

None of these are required — `papers_discover` and the citation tools work anonymously out of the box. Setting them just raises rate limits / reliability on the respective provider:

| Variable | Effect |
|----------|--------|
| `S2_API_KEY` | Semantic Scholar API key — higher rate limit for search + citations. |
| `TOO_MANY_PAPERS_CONTACT_EMAIL` | Sent to OpenAlex as the "polite pool" contact — more reliable anonymous access. |
| `ARXIV_MIN_INTERVAL`, `OPENALEX_MIN_INTERVAL`, `S2_MIN_INTERVAL` | Minimum seconds between requests to each provider, if you need to tune throttling. |

## Usage

Just talk about papers. On first use, the skill gives a brief self-introduction, then 
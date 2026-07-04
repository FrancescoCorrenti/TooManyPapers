# Too Many Papers

A local research assistant that keeps a knowledge graph of the papers, concepts, and projects you work on. No cloud, no database.

## What's included

- A **skill** that handles onboarding, briefings, and behavior rules.
- An **MCP server** with tools to manage papers, venues, and the knowledge graph, including automatic PDF fetching.
- A **web UI** to browse papers and the graph visually.

<p align="center">
  <img src="../docs/screenshots/01-papers-browse.gif" width="700" alt="Papers tab">
  &nbsp;
  <img src="../docs/screenshots/02-graph-view.gif" width="700" alt="Graph view">
</p>

## Setup

Requires **[uv](https://docs.astral.sh/uv/)**.

- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Node.js 18+ is only needed for the web UI. Once installed, the MCP server starts automatically.

### Optional environment variables

Discovery and citations work without these, but you'll hit rate limits sooner or later. Both keys below are free.

| Variable | What it's for |
|----------|--------------|
| `S2_API_KEY` | [Semantic Scholar](https://www.semanticscholar.org/product/api#api-key-form) key, higher rate limit |
| `OPENALEX_API_KEY` | [OpenAlex](https://openalex.org/settings/api) key, needed for reliable search |
| `UNPAYWALL_EMAIL` | Contact email for Unpaywall, used for automatic PDF fetching |
| `TOO_MANY_PAPERS_CONTACT_EMAIL` | Fallback contact email if the above aren't set |

## Usage

Just talk about papers:

- "I just read this paper: [link], add it to my library"
- "What should I read next on segmentation?"
- "Give me today's paper briefing"
- "Connect this paper to my FCD project"

On first use, the AI asks what you're working on and drafts a starting set of concepts and projects to confirm.

To open the web UI, run `/too-many-papers:webui` or ask to open Too Many Papers. It opens at http://localhost:3737.

<p align="center">
  <img src="../docs/screenshots/03-pdf-viewer.gif" width="700" alt="Inline PDF viewer">
  &nbsp;
  <img src="../docs/screenshots/05-advanced-filters.gif" width="700" alt="Advanced filters">
</p>

## Data

Everything lives in plain JSON files (`_papers.json`, `_venues.json`, `_graph.json`), plus a `pdfs/` folder and a `_log.jsonl` audit log, always at `~/.too-many-papers` — a fixed path in your home directory, independent of OS, host, or plugin install location, so it survives plugin updates and never resets. Back it up by copying that folder.

## License

MIT

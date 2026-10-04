# Too Many Papers

A local research assistant that keeps a knowledge graph of the papers, concepts, and projects you work on. No cloud, no database.

## What's included

- A **skill** that handles onboarding, the read-and-link workflow, and behavior rules.
- An **MCP server** with tools to manage papers, citations, and project–paper analyses, including automatic PDF fetching.
- A **web UI** to browse papers and the graph visually.

<p align="center">
  <img src="../docs/screenshots/demo.gif" width="700" alt="Too Many Papers demo">
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
| `TOO_MANY_PAPERS_DATA_DIR` | Optional isolated data directory for development/tests; defaults to `~/.too-many-papers` |

## Usage

Just talk about papers:

- "I just read this paper: [link], add it to my library"
- "What should I read next on segmentation?"
- "Which of my project analyses are out of date?"
- "Connect this paper to my FCD project"

On first use, the AI asks what you're working on and drafts a starting set of concepts and projects to confirm.

To open the web UI, run `/too-many-papers:webui` or ask to open Too Many Papers. It opens at http://localhost:3737.

Projects connect only to papers. Every association requires an LLM-written relevance idea,
with the useful findings, evidence and limitations. `graph_project_context` retrieves those
ideas; `graph_review_queue` identifies changed context and migrated ideas requiring analysis.
Old work/milestone nodes are preserved as ideas during the automatic schema 3.0 migration,
with a backup of the original graph. Project planning remains up to the user.

## Data

Everything lives in plain JSON files (`_papers.json`, `_venues.json`, `_graph.json`), plus a `pdfs/` folder and a `_log.jsonl` audit log, always at `~/.too-many-papers` — a fixed path in your home directory, independent of OS, host, or plugin install location, so it survives plugin updates and never resets. Back it up by copying that folder.

## License

MIT

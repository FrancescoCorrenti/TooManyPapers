# Too Many Papers

A local research assistant that keeps a knowledge graph of the papers, concepts, and projects you work on. No cloud, no database. Just JSON files and an MCP server.

This repo is both the plugin and a plugin marketplace, so it installs in one command.

## Install

### Claude Code / Claude Desktop

```
/plugin marketplace add FrancescoCorrenti/too-many-papers
/plugin install too-many-papers@too-many-papers
```

Restart Claude after installing.

### Cowork

1. Open **Customize** > **Plugins**.
2. Click **Add** > **Add from repository**.
3. Paste `https://github.com/FrancescoCorrenti/TooManyPapers`.
4. Sync, then add the **too-many-papers** plugin.

Restart Claude after installing.

### Requirements

- **[uv](https://docs.astral.sh/uv/)**, to run the MCP server.
  - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Node.js 18+**, only needed for the web UI.

## Usage

Just talk about papers:

- "I just read this paper: [link], add it to my library"
- "What should I read next on segmentation?"
- "Give me today's paper briefing"
- "Connect this paper to my FCD project"

On first use, the AI asks what you're currently working on and drafts a starting set of concepts and projects for you to confirm.

To browse visually, run `/too-many-papers:webui` or ask to open Too Many Papers. It opens at http://localhost:3737.

<p align="center">
  <img src="docs/screenshots/demo.gif" width="700" alt="Too Many Papers demo">
</p>

The web UI has a tab per type (Papers, Concepts, Projects, Endpoints, Ideas, Pools, Notes, Venues) plus a graph view, search and filters everywhere, an inline PDF reader where you can select text to add a note, and a pencil icon to edit anything.

PDFs are fetched automatically from arXiv, Semantic Scholar, and Unpaywall when a paper is added, so there's usually something to read right away.

## Features

- Knowledge graph linking papers, concepts, projects, endpoints, ideas, pools, and reading notes.
- All data in plain JSON at `~/.too-many-papers`, no cloud and no database, kept across plugin updates.
- Conversational management through Claude: add, update, hide, or delete papers by talking.
- Bulk tools to add or remove many papers, venues, or graph nodes and edges in one call.
- Automatic paper discovery across arXiv, Semantic Scholar, and OpenAlex.
- Duplicate detection before anything is added.
- Automatic PDF fetching from arXiv, Semantic Scholar, and Unpaywall.
- Automatic citation linking from Semantic Scholar, including backlinks and unresolved references.
- Daily briefings and "what should I read next" suggestions.
- Engagement tracking, so the graph reflects what you actually work on.
- Web UI with a tab per type, plus search, filters, and sort on every tab.
- Force-directed graph view (d3-force) with a clustered layout and organic node dragging.
- Graph filters by node type, edge type, and hop depth, with searchable multi-select "linked to" and "concepts" filters.
- Saveable filter presets.
- Missing papers view: entries cited by your library but not in it, click to search them on the web.
- Inline PDF reader where selecting text turns it into a note attached to the paper.
- Edit any paper, venue, or graph item from the UI.
- BibTeX export from Claude or the web UI, for the whole library, a selection, or a single paper.
- A library.bib file kept in sync automatically on every change.
- Output validation on export that flags missing years, missing DOIs or URLs, unverified metadata, and duplicate keys.
- Stable citation keys that do not shift as the library grows.
- Optional free API keys for higher rate limits.

## FAQ

**Where is my data stored?**
Always at `~/.too-many-papers`, a fixed path in your home directory. 

**How do I update?**
`/plugin marketplace update` then `/plugin update too-many-papers@too-many-papers`.

## License

MIT

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

### Cursor

The MCP server works natively in Cursor. Add it to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "too-many-papers": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/too-many-papers-plugin/server", "_scripts/mcp_server.py"]
    }
  }
}
```

Replace the path with where you cloned this repo. Skills and slash commands are Claude Code-only and won't be available in Cursor — just the MCP tools (papers, graph, briefing, etc.).

### Requirements

- **[uv](https://docs.astral.sh/uv/)**, to run the MCP server.
  - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Node.js 18+**, only needed for the web UI.

## Usage

Just talk about papers:

- "I just read this paper: [link], add it to my library"
- "What should I read next on transformers?"
- "Give me today's paper briefing"
- "Connect this paper to my RAG project"

On first use, the AI asks what you're currently working on and drafts a starting set of concepts and projects for you to confirm.

To browse visually, run `/too-many-papers:webui` or ask to open Too Many Papers. It opens at http://localhost:3737.

<p align="center">
  <img src="docs/screenshots/demo.gif" width="700" alt="Too Many Papers demo">
</p>

The web UI has a tab per type (Papers, Concepts, Projects, Endpoints, Ideas, Pools, Notes, Venues) plus a graph view, search and filters everywhere, an inline PDF reader where you can select text to add a note, and a pencil icon to edit anything.

PDFs are fetched automatically from arXiv, Semantic Scholar, and Unpaywall when a paper is added, so there's usually something to read right away.

## Features

- **It reads with you.** The graph tracks the concepts, projects, and ideas behind what you read, and quietly learns what you're actually into.
- **Finds papers for you.** One ask hits arXiv, Semantic Scholar, and OpenAlex at once, follows citations to surface more, and drops the duplicates.
- **Grabs the PDF automatically.** Pulled from arXiv, Semantic Scholar, or Unpaywall the moment a paper lands, so there's usually something to read right away.
- **Wires up citations.** Connects each paper to what it cites and what cites it, and flags the references you don't have yet.
- **Read, highlight, remember.** Select text in the built-in PDF reader and it becomes a note pinned to the paper and a node in your graph.
- **A graph you can actually explore.** Force-directed, clustered by topic, draggable without the whole thing exploding, with a "missing papers" layer for everything cited but not yet in your library.
- **Export that just works.** One click to BibTeX, with stable cite keys and an output check that catches anything broken before it reaches your `.bib`.
- **Yours, on your disk.** Plain JSON at `~/.too-many-papers`. No cloud, no database, no lock-in.

## FAQ

**Where is my data stored?**
Always at `~/.too-many-papers`, a fixed path in your home directory. 

**How do I update?**
`/plugin marketplace update` then `/plugin update too-many-papers@too-many-papers`.

## License

MIT

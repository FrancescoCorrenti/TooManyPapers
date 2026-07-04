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

## FAQ

**Where is my data stored?**
Always at `~/.too-many-papers`, a fixed path in your home directory. Same on every OS and host (including Cowork), independent of the plugin's install location, so it survives plugin updates, reinstalls, and session resets.

**Can I use this without an LLM?**
Yes, the web UI and the underlying CLI both work on their own.

**How do I back up my data?**
Copy the data folder shown by `graph_status`, or just keep a copy of the JSON files.

**How do I update?**
`/plugin marketplace update` then `/plugin update too-many-papers@too-many-papers`.

## License

MIT

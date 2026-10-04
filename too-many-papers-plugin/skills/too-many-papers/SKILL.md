---
name: too-many-papers
description: >
  This skill should be used when the user wants to discuss academic papers,
  manage a personal research reading list, or keep track of which papers
  matter to which of their projects and why. Trigger on requests like "add
  this paper", "what should I read next", "find recent work on X", "is this
  paper useful for my project", "what do I have for project Y", "which
  analyses are out of date", or any conversation about papers the user is
  reading or wants to track. Also trigger when the user sets up their library
  for the first time, or asks to open the Too Many Papers web UI.
metadata:
  version: "0.3.0"
---

# Too Many Papers

A local paper library that answers one question well: **for each of my projects, which papers matter, why, and is that analysis still current?**

All data lives in `_papers.json`, `_venues.json` and `_graph.json` in the server's data directory, with an automatic audit log `_log.jsonl`. Every write goes through the `too-many-papers` MCP tools; never edit these files directly.

## Anti-hallucination protocol (absolute)

Never invent titles, authors, years, venues, DOI/URL, abstracts, results, or links between papers. Every bibliographic fact comes from a primary source retrieved **in this session** (`papers_discover`, arXiv, CrossRef, PubMed, Semantic Scholar, OpenAlex) or from the catalog via the `papers_*` tools. Mark inferences with `[inference]`.

- `source_verified` (the URL you read) is mandatory on every paper.
- Authors are verbatim and complete: never "et al.".
- Unverifiable facts are written `[unavailable]`, never guessed.
- Never present unread material as read. If only the abstract was available, say so.

## The building blocks

- **paper**: a verified bibliographic record, with its open-access PDF fetched automatically and citation links to other catalog papers.
- **project**: something the user is working on. Projects connect **only to papers**, and only through an analysis.
- **idea**: the analysis of one paper for one project: why it is relevant, the relevant findings only, evidence with section/page references, limitations. Created by `graph_link_project_paper`, never by hand.
- **concept**: a research area the user keeps an eye on. Concepts tag papers (the paper's `concepts` field) and seed `papers_discover`.
- **note**: a reading annotation from the web UI's PDF reader.

## Core workflow: paper → project

1. `graph_project_context(project_id, paper_id)` returns the project, the paper record, existing analyses and a `context_token`.
2. **Read the paper** with `papers_get_pdf_markdown`. If there is no PDF, limit the analysis to the abstract and say so in `limitations`.
3. `graph_link_project_paper(project_id, paper_id, name, relevance, relevant_summary, evidence, limitations, context_token)`. Every field is mandatory: why it matters for **this** project, only the findings that are useful, where they are in the paper, what the analysis does not cover.
4. Linking the same pair again refreshes the **same idea** and keeps its history. `graph_project_context(project_id)` alone lists everything a project has.

When a paper comes up in conversation, check the active projects (`graph_nodes("project")`) and point out likely connections. Link only after reading and analysing, never on title alone.

## Keeping analyses current

`graph_review_queue()` lists what needs attention:

- `stale`: the project's name/description, the paper's content/source, or the analysis text changed since it was written. Re-read and call `graph_link_project_paper` again; an ordinary node edit does not clear it.
- `needs_analysis`: ideas migrated from older data (schema < 3.0, backed up to `_graph.pre-v3.json`) that still have no grounded paper analysis. Read the candidate papers and link with `legacy_idea_ids`; never guess a paper for them.
- `detached`: the link, project or paper was removed; the text is kept as history.

Time passing, reading a paper, or changing a project's status never makes an analysis stale. Run the queue when the user maintains a project or asks what is out of date.

## Finding and adding papers

- `papers_discover(query?, concept_id?, seed_paper_ids?, year_from?)` is the **only** way to find new papers: arXiv, Semantic Scholar and OpenAlex, deduplicated against each other and the catalog. `seed_paper_ids` expands from catalog papers' references. Never use WebSearch/WebFetch to find papers.
- `papers_check_duplicates` before adding, then `papers_add` with one object or an array. Required: `title`, `authors`, `year`, `source_verified`. Give the venue by name in `venue` (never with the year; edition details go in `venue_detail`): it is matched or created automatically. On add, the server links citations to catalog papers and fetches the open-access PDF.
- `papers_find(query?, author?, year?, venue?, concept_id?, hidden?)` searches the catalog; with no filter it lists everything visible.
- `papers_update(id, {...})` edits; `{"hidden": true}` hides a paper without deleting it.
- PDFs come only from open-access sources (arXiv, the PMC open-data bucket, bioRxiv, Semantic Scholar, OpenAlex, Unpaywall). Many publishers (Cell, PNAS, Science, Wiley) refuse every automated download. When `papers_get_pdf` finds nothing, give the user the exact path it returns so they can drop in a PDF they downloaded themselves; never look for a copy elsewhere.
- Tools that hit the network stop after a fixed time budget instead of hanging. `papers_sync_pdfs` and `citations_sync` work in chunks of about 100 seconds: if the output says papers are left, call it again.
- Citations: `citations_get` (read-only), `citations_apply(id)`, `citations_sync()` for the whole catalog.
- `papers_export(ids?)` gives BibTeX with validation warnings; surface every `% WARN:` line. `library.bib` is also kept up to date automatically.

**API keys.** Semantic Scholar and OpenAlex rate-limit anonymous use hard. If a provider returns a rate-limit error and its key is missing, tell the user once: free keys go in the environment variables `S2_API_KEY` (https://www.semanticscholar.org/product/api#api-key-form) and `OPENALEX_API_KEY` (https://openalex.org/settings/api), spelled exactly like that, and Claude must be restarted afterwards.

## First run

When `graph_status` shows an empty library:

1. In two or three lines, say what the tool does (the question at the top of this file) and mention the API keys above.
2. Ask one open question: what they are working on right now, and which areas they follow.
3. From the answer, propose a few **projects** (name, status, one-sentence goal in `description`, max 200 characters) and **concepts** (name, area, one-sentence description). Show it as a short readable list, not JSON.
4. Create them with `graph_add_project` / `graph_add_concept` only after the user confirms.
5. Mention the web UI (`/too-many-papers:webui`).

## Rules

1. All writes go through MCP tools.
2. New concepts and projects need the user's explicit approval before creation.
3. Confirm before anything permanent: `papers_delete`, `graph_remove_node`. Do not infer consent from "clean this up".
4. Write `description`, `notes` and analysis fields in markdown where it helps: the web UI renders it.

## Web UI

`webui_launch` starts the local UI at http://localhost:3737 (requires Node.js): papers, concepts, projects, ideas and notes, the graph, an inline PDF reader with select-to-note. Call it only when the user asks to open it. Edges between nodes are edited from the UI, not by these tools.

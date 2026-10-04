# Changelog

User-facing changes per release. Write new entries under **Unreleased** as
you go; `python scripts/release.py X.Y.Z` turns that section into the release
notes, tags `vX.Y.Z` and points the marketplace at it. `main` is development:
installed plugins only move when a release is cut.

## Unreleased

**Too Many Papers now does one thing well: for each of your projects, which papers matter, why, and whether that analysis is still current.**

### New
- **Project ↔ paper analyses.** A project links to a paper only through an analysis written after reading it: why it matters for this project, the relevant findings, evidence with section/page references, and limitations. Re-linking refreshes the same analysis and keeps its history.
- **Review queue.** `graph_review_queue` lists analyses that went stale because the project's goal or the paper changed, plus legacy ideas still waiting for a grounded analysis.
- **`papers_find`**: one search with optional filters (text, author, year, venue, concept, hidden).
- **Simpler `papers_add`**: one paper or a list; only title, authors, year and `source_verified` are required; the venue is given by name and created if new.
- **More PDFs found.** PMC articles now come from PMC's open-data bucket instead of a URL that only served a cookie stub, and every open-access copy OpenAlex knows about is tried. On a 457-paper library this recovered 21 of 162 missing PDFs in the first two sync runs.

### Fixed
- **MCP calls no longer hang.** Network work runs under a time budget (45 s per call, 30 s per added paper). `papers_sync_pdfs` and `citations_sync` work in ~100 s chunks and resume where they stopped.
- arXiv IDs were being read out of DOIs (e.g. `…compmedimag.2025.102562`), sending PDF fetches to non-existent arXiv papers.

### Changed
- Web UI visual overhaul; graph edits from the UI go through the same validation as the MCP tools.
- Graph schema 3.0. On first access the old graph is backed up to `_graph.pre-v3.json`; waypoints and endpoints become ideas with their text and provenance preserved, queued for review. Nothing is deleted and no relevance is invented.

### Removed (breaking)
- Daily briefing (`briefing_*`), engagement tracking (`graph_interact`, `graph_engagement`), `graph_lint`, `graph_path`, `graph_neighbors`.
- Venue management tools (`venues_*`): venues are now created from papers automatically.
- `papers_list`, `papers_search`, `papers_by_*`, `papers_outside`, `papers_hidden`, `papers_next_id` → use `papers_find`.
- `papers_hide` / `papers_unhide` → `papers_update` with `{"hidden": true|false}`.
- All `*_bulk` tools → the base tools accept lists.
- Generic edge tools and `graph_add_idea` / `graph_add_note` over MCP (edges and notes are still edited from the web UI).
- `papers_fetch_pdf` → `papers_get_pdf` fetches when the PDF is missing.

## 0.2.0

Last release before the changelog was kept.

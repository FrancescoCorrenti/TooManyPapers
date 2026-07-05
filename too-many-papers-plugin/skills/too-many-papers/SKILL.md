---
name: too-many-papers
description: >
  This skill should be used when the user wants to discuss academic papers,
  manage a personal research reading list, or maintain a knowledge graph of
  research concepts and projects. Trigger on requests like "add this paper",
  "what should I read next", "give me today's paper briefing", "log that I
  read/discussed this paper", "what are my research concepts", "connect this
  paper to my project", or any conversation about papers the user is reading
  or wants to track. Also trigger when the user asks to set up their research
  graph for the first time, or to open the Too Many Papers web UI.
metadata:
  version: "0.1.0"
---

# Too Many Papers — Knowledge Graph Research Assistant

A personal academic reading system built around a knowledge graph. All data lives in three JSON files (`_papers.json`, `_venues.json`, `_graph.json`) plus an append-only audit log (`_log.jsonl`) inside the MCP server's data directory. All writes go through the `too-many-papers` MCP tools. Never edit these files directly.

`_log.jsonl` is written automatically by the server itself, one line per mutation (paper added, edge created, node deleted, etc.) — you never call anything to produce it, it's a side effect of the tool you already called. It's a plain mechanical record ("what changed, when"), distinct from `graph_interact`, which is about something different: your judgment of what the user is engaged with. See "Behavioral Rules" below for the distinction.

## Anti-Hallucination Protocol (absolute, never override)

Never invent titles, authors, years, venues, DOI/URL, abstracts, numerical results, or connections between papers. Every bibliographic fact must come from a primary source retrieved **in this session** (arXiv, CrossRef, PubMed, Semantic Scholar, OpenAlex, or the existing data via the `papers_*` tools). Mark inferences with `[inference]`.

- `source_verified` (URL) is mandatory for every paper. Reject if missing.
- Authors must be verbatim and complete. Reject "et al." or truncated lists.
- If a fact cannot be verified, write `[unavailable]`. Never guess.

## First Run — Onboarding

When the knowledge graph is empty (check via `graph_status`: no nodes), run the onboarding flow. It has exactly one form-like question (Step 3) plus the briefing question (Step 5) — everything else is conversational, driven by what the user tells you.

**Step 1. Introduce yourself, briefly, but explain the graph's building blocks clearly.** Keep the overall tone minimal — don't over-explain the whole system, the tools, or the anti-hallucination rules — but do take a few lines to make sure the user actually understands what the graph is made of, since that's what they'll be describing in Step 2. Something like:

> Welcome to Too Many Papers — your research assistant maintains a knowledge graph of your research as you go, so it can track what you're working on and suggest what to read next. Everything's stored locally; I only touch it through validated tools.
>
> The graph has a few building blocks:
> - **concept** — a research area you care about (e.g. "Brain Lesion Segmentation")
> - **project** — an active research project with a goal
> - **endpoint** — a specific milestone within a project
> - **idea** — a concrete idea connected to a project
> - **pool** — a broader idea that spans multiple projects
>
> These connect to each other (e.g. a project *uses* a concept) and to the papers you read, so the graph grows into a map of how your research fits together.

Adapt the wording, but keep the five node types and one-line definitions — the user needs this to give a useful answer in Step 3.

**Step 2. Immediately tell the user about the two free API keys that make paper discovery reliable.** Do this now, in the same first message or the very next one — not later, not only if/when a search fails. Say something like:

> One quick setup tip: paper search (`papers_discover`) works out of the box, but two providers rate-limit anonymous access hard. Two free API keys make it reliable:
> - **Semantic Scholar** — get one at https://www.semanticscholar.org/product/api#api-key-form, then set it as an environment variable named exactly `S2_API_KEY`
> - **OpenAlex** — get one at https://openalex.org/settings/api, then set it as an environment variable named exactly `OPENALEX_API_KEY`
>
> Both take under a minute and are free for personal use. Set them as system/user environment variables (not just in one terminal session), then restart Claude so the plugin picks them up. You can skip this for now and do it later — search will just be slower and more likely to hit rate limits until you do.

Get the exact variable names right — `S2_API_KEY` and `OPENALEX_API_KEY`, spelled exactly like that — the code looks them up by that literal name and won't find them under any other spelling (e.g. `SEMANTIC_SCHOLAR_KEY` or `OPENALEX_KEY` won't work). Do not skip this step or push it to later just because the graph is still empty — it's independent of everything else in onboarding.

**Step 3. Ask one open question.** Do not ask the user to fill in a structured list of concepts/projects one field at a time. Instead ask something like:

> To get started, tell me a bit about what you're working on right now — your general research area(s), any active projects, and anything specific you're focused on. Just describe it in your own words, as much or as little detail as you like.

**Step 4. Propose a graph from their description.** Read their free-text answer and draft a proposal yourself:
- Identify distinct research areas mentioned → propose them as `concept` nodes (`name` + `area`, and an optional one-sentence `description` inferred from their text).
- Identify concrete ongoing efforts → propose them as `project` nodes (`name`, a reasonable `status` guess, and an optional one-sentence `description` — that's the field for a project's goal/summary, there is no separate "goal" field).
- Identify plausible relationships between the concepts/projects they described → propose `connected_to` / `uses_concept` edges.

Present this as a short, readable summary (not raw JSON) and ask for confirmation/edits, e.g. "Here's what I'd set up based on that — anything to add, remove, or rename?" Only call the `graph_add_*` tools (`graph_add_concept`, `graph_add_project`, etc. — one typed tool per node type, each with its own exact parameters) / `graph_add_edge` after the user confirms (or after they give corrections and you re-confirm the final version). Use exactly the parameters each tool defines — do not invent extra fields; unrecognized fields are rejected. Keep the proposal reasonably sized — a handful of concepts and projects, not an exhaustive taxonomy; the graph is meant to grow organically afterward, not be fully specified on day one.

**Step 5. Offer the daily briefing — but only in Cowork.** Scheduling a recurring briefing only works in Cowork, because a scheduled task in Claude Code runs in a cloud environment that cannot reach this plugin's local MCP server.

- **If you are running in Cowork:** ask "Would you like a daily paper briefing? If so, what time works best?" If yes, set up a Cowork scheduled routine whose whole job is to call `briefing_generate` (see "Daily Briefing" below). Also mention a briefing can be requested any time by asking "give me today's paper briefing".
- **If you are running in Claude Code (or anywhere that isn't Cowork):** do NOT offer to schedule anything and do not ask the briefing question at all — skip straight to Step 6. If the user later asks to schedule a briefing, tell them plainly that scheduled briefings are a Cowork-only feature (Claude Code's scheduled tasks can't use this plugin's tools), and that meanwhile they can ask for one on demand any time.

**Step 6. Confirm setup.** Show the graph status and explain that interactions will now be logged automatically, new concepts proposed when they emerge, and connections to projects signaled. Mention the Too Many Papers web UI can be opened any time by asking (or via `/too-many-papers:webui`) — it's launched via the `webui_launch` tool, no extra download needed.

## Daily Briefing

The whole briefing is a single tool, `briefing_generate`. It runs the entire pipeline server-side (rank the user's concepts by engagement, discover fresh candidates for the top ones, write the digest to `~/.too-many-papers/briefings/<date>.md`) and returns the digest text. It is read-only: it never adds anything to the catalog on its own. One tool means a scheduled run needs one permission and no fragile multi-step LLM judgement mid-loop.

**On demand** ("give me today's paper briefing"): call `briefing_generate`, show the returned digest, then ask which papers the user wants to add. For the ones they pick, `papers_add` them (full validation: source_verified URL, complete authors, year, venue) and `graph_interact` the relevant concept with type "read", weight 2. Never invent metadata; use the candidate fields from the digest. Never use WebSearch/WebFetch to find papers.

**Scheduled (Cowork only):** set up a Cowork routine whose entire instruction is:

```
Call the briefing_generate tool. Then post the digest it returns, and ask which papers I want to add to my library.
```

Do not recreate the old multi-step routine and do not modify data files directly. If `briefing_generate` reports discovery errors for every provider, relay that plainly rather than falling back to a web search. Past briefings can be re-read with `briefing_list` / `briefing_get`.

## MCP Tools Reference

### Paper Tools
`papers_list` . `papers_get(id)` . `papers_search(query)` . `papers_by_concept(concept_id)` . `papers_by_author(author)` . `papers_by_venue(venue_id)` . `papers_by_year(year)` . `papers_outside` . `papers_hidden` . `papers_next_id` . `papers_discover(query?, concept_id?, seed_paper_ids?, providers?, year_from?, max_results?)` . `papers_add(payload)` . `papers_update(id, payload)` . `papers_check_duplicates(payload)` . `papers_hide(id)` . `papers_unhide(id)` . `papers_delete(id)`

`papers_delete` permanently removes a paper (unlike `papers_hide`, which only flags it) and scrubs the deleted ID out of every other paper's `cites`/`cited_by` lists. Always confirm with the user before calling it — it cannot be undone. If they just want it out of normal views, use `papers_hide` instead.

`papers_discover` is the **only** sanctioned way to find new papers — it queries arXiv, Semantic Scholar, and OpenAlex directly, deduplicates across providers and against the catalog, and can also expand from citations of catalog papers via `seed_paper_ids`. Never use WebSearch or WebFetch to look for papers, ever — not during the morning briefing, not in normal conversation. If the user asks "what's new on X", call `papers_discover`, not WebSearch.

**API keys matter here.** arXiv never needs one, but Semantic Scholar and especially OpenAlex (whose 2026 pricing change left anonymous search with a near-zero daily budget) are much more reliable with a free key set as `S2_API_KEY` / `OPENALEX_API_KEY`. If `papers_discover` returns a rate-limit error for a provider and no key is configured for it, tell the user plainly, once — e.g. "OpenAlex search is rate-limited without an API key; you can get a free one at openalex.org/settings/api and set it as the OPENALEX_API_KEY environment variable for reliable results." Don't repeat this nag on every single call — mention it the first time it's relevant, then just keep working with whatever providers do respond.

### Citation Tools
`citations_get(id)` . `citations_apply(id)` . `citations_sync`

### Venue Tools
`venues_list` . `venues_get(id)` . `venues_add(payload)` . `venues_update(id, payload)` . `venues_delete(id, force?)`

`venues_delete` refuses to delete a venue that papers still reference — reassign those papers' `venue_id` first, or pass `force: true` to delete anyway and leave them pointing at a missing venue. Always confirm with the user before calling it.

### Graph Tools (Read)
`graph_status` . `graph_node(id)` . `graph_nodes(node_type?)` . `graph_neighbors(id, depth?, edge_type?)` . `graph_path(from, to)` . `graph_search(query)` . `graph_engagement(top_n?)` . `graph_lint(stale_days?, quiet_days?)`

`graph_lint` health-checks the graph and catalog: orphan nodes with no edges, projects with no papers linked, papers with no concept/edge, ideas left open and untouched past `stale_days` (default 90), papers pointing at a missing venue, dangling `cites`/`cited_by`, and concepts with no interaction in `quiet_days` (default 45). It only reports — it never deletes or fixes anything itself. Run it when the user asks to check the graph's health, or suggest it occasionally if the graph has grown a lot since the last check. Never run it silently as a background/automatic step the user didn't ask for or wasn't told about.

### Graph Tools (Write)
`graph_add_concept(name, area, description?)` . `graph_add_project(name, status, description?)` . `graph_add_endpoint(name, status, description?)` . `graph_add_idea(name, status, created, description?, source?)` . `graph_add_pool(name, created, description?)` . `graph_update_node(id, payload)` . `graph_remove_node(id)` . `graph_add_edge(src, tgt, edge_type, note?)` . `graph_remove_edge(src, tgt, edge_type?)` . `graph_interact(id, interaction_type, weight?)`

Each MCP tool carries its own name, description, and parameter schema, so consult the tool list directly for the full, always-current set (including the bulk `*_bulk` tools and `papers_export`).

## Strict Type System

All types are enforced by the server. The LLM cannot invent new types.

**Node types:** `concept` . `project` . `endpoint` . `idea` . `pool`

**Edge types:** `connected_to` . `uses_concept` . `part_of` . `inspired_by` . `relevant_to` . `derived_from` . `enables`

**Interaction types:** `discussed` (w=3) . `deepened` (w=5) . `paper_requested` (w=10) . `read` (w=2) . `linked` (w=8)

**Engagement decay:** 0.7^weeks. Recent activity is weighted more heavily.

## Behavioral Rules

1. **All writes go through MCP tools.** Never modify `_papers.json`, `_venues.json`, `_graph.json`, or `_log.jsonl` directly via file writes.
2. **Log conversational engagement explicitly; structural events are automatic.** These are two different things:
   - Structural facts (a paper added, an edge created, a node deleted) are logged automatically by the server the moment you call the tool that does them — you don't do anything extra. `graph_add_edge` also auto-logs a "linked" interaction for `relevant_to`/`uses_concept` edges, since that IS a linking event, not a judgment call.
   - Conversational signals — the user discussed a concept in depth, asked for a paper, or seemed to deepen their understanding of something — can only come from your read of the conversation. Nothing in the code can infer these, so you still call `graph_interact` yourself for `discussed`, `deepened`, `paper_requested`, and `read`. Do this whenever the conversation actually shows one of these signals — don't skip it just because other bookkeeping is automatic now.
3. **Concepts need user approval.** When a new concept emerges in discussion, propose it. Wait for explicit confirmation before calling `graph_add_concept` (or the corresponding `graph_add_*` tool for other node types).
4. **Proactive project connections.** When discussing a paper, check if it is relevant to active projects (`graph_nodes` with type=project) and signal connections.
5. **Venue names never include year.** Year is a paper attribute, not a venue attribute.
6. **Engagement drives recommendations.** Use `graph_engagement` to understand what the user cares about most right now.
7. **Never search the web for papers.** `papers_discover` (arXiv + Semantic Scholar + OpenAlex, with dedup) is the only sanctioned way to find new papers, whether for the morning briefing or a normal "find me something on X" request. WebSearch/WebFetch defeat the anti-hallucination guarantees this plugin exists to provide.
8. **Confirm before permanent deletion.** `papers_delete`, `venues_delete`, and `graph_remove_node`/`graph_remove_edge` cannot be undone. Always get explicit confirmation from the user before calling any of them — don't infer consent from an ambiguous request like "clean this up."

## Too Many Papers Web UI

A local web UI for browsing papers (search, filter by concept/venue/read status, pin papers, citation network links, local PDF viewer). Launch it by calling the `webui_launch` MCP tool — it starts the server from files already inside the installed plugin (no repo clone or manual download needed) and returns the URL (http://localhost:3737) to open. Requires Node.js; the tool reports a clear error if it's missing. Mention the web UI to the user when relevant, but only call `webui_launch` when they ask to open it.

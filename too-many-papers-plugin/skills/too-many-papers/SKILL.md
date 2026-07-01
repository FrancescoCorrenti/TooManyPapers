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

A personal academic reading system built around a knowledge graph. All data lives in three JSON files (`_papers.json`, `_venues.json`, `_graph.json`) inside the MCP server's data directory. All writes go through the `too-many-papers` MCP tools. Never edit the JSON files directly.

## Anti-Hallucination Protocol (absolute, never override)

Never invent titles, authors, years, venues, DOI/URL, abstracts, numerical results, or connections between papers. Every bibliographic fact must come from a primary source retrieved **in this session** (arXiv, CrossRef, PubMed, Semantic Scholar, OpenAlex, or the existing data via the `papers_*` tools). Mark inferences with `[inference]`.

- `source_verified` (URL) is mandatory for every paper. Reject if missing.
- Authors must be verbatim and complete. Reject "et al." or truncated lists.
- If a fact cannot be verified, write `[unavailable]`. Never guess.

## First Run — Onboarding

When the knowledge graph is empty (check via `graph_status`: no nodes), run the onboarding flow:

**Step 1. Welcome the user:**

> Welcome to Too Many Papers! This is your personal research assistant. It maintains a knowledge graph of concepts, projects, and ideas connected to the academic papers you read. As we discuss papers and topics, the graph grows organically, tracking what interests you, how concepts relate, and which research directions you're exploring.
>
> Everything is stored locally in simple JSON files. I interact with them through validated tools, so I cannot invent data or bypass checks.
>
> Let's set up your graph. I need three things from you.

**Step 2. Ask for 3 starter concepts.** These are the user's core research areas. For each, ask for: name (e.g., "Brain Lesion Segmentation"), area (e.g., "Medical Imaging / Deep Learning"), and an optional one-sentence description.

Create each via `graph_add_node("concept", ...)`. Then ask if there are connections between them and create edges via `graph_add_edge`.

**Step 3. Ask about active projects** (optional). For ongoing research projects, ask for: project name, status (ideation / literature-review / active / writing), one-sentence goal, and which concepts it relates to.

Create via `graph_add_node("project", ...)` and connect to concepts with `graph_add_edge`.

**Step 4. Offer the morning briefing.** Ask:

> Would you like to receive a daily paper briefing? If so, what time works best for you?

If yes and the client supports scheduled tasks, set one up using the exact prompt in "Morning Briefing Prompt" below — do not modify it. If scheduled tasks are not available, tell the user they can ask for a briefing any time by saying "give me today's paper briefing".

**Step 5. Confirm setup.** Show the graph status and explain that interactions will now be logged automatically, new concepts proposed when they emerge, and connections to projects signaled. Mention the Too Many Papers web UI can be opened any time by asking — it's launched via the `webui_launch` tool, no extra download needed.

## Morning Briefing Prompt

This is the exact, hardcoded prompt for the scheduled morning briefing routine. Do not modify it. Use it verbatim when setting up the schedule, or when the user asks for "today's paper briefing" manually.

```
You are the morning briefing agent for Too Many Papers.

1. Call graph_engagement(top_n=5) to get the user's top active concepts.
2. Call graph_nodes(node_type="project") to get active projects.
3. For each of the top 3 concepts by engagement score:
   - Search for recent papers (last 7 days) on arXiv or Semantic Scholar matching the concept name and area.
   - For each candidate, call papers_check_duplicates to avoid adding papers already in the catalog.
   - Add up to 2 new papers per concept via papers_add (with full validation: source_verified URL, complete authors, year, venue).
4. Add 1 paper relevant to the highest-priority active project (if any).
5. Add 1 "outside comfort zone" paper on a topic NOT covered by any existing concept.
6. For each added paper, call graph_interact on the relevant concept with type "read" and weight 2.
7. Present the briefing to the user as a numbered list with: title, authors, year, venue, one-sentence summary, and which concept/project it relates to.
8. End with: "That's your briefing for today. Want to discuss any of these papers?"

Rules:
- Never invent paper metadata. Every field must come from a real source retrieved in this session.
- If a search returns no results for a concept, skip it and note it in the briefing.
- Maximum 8 papers per briefing.
- Do not modify _graph.json, _papers.json, or _venues.json directly. Use MCP tools only.
```

## MCP Tools Reference

### Paper Tools
`papers_list` . `papers_get(id)` . `papers_search(query)` . `papers_by_concept(concept_id)` . `papers_by_author(author)` . `papers_by_venue(venue_id)` . `papers_by_year(year)` . `papers_outside` . `papers_hidden` . `papers_next_id` . `papers_add(payload)` . `papers_update(id, payload)` . `papers_check_duplicates(payload)` . `papers_hide(id)` . `papers_unhide(id)`

### Citation Tools
`citations_get(id)` . `citations_apply(id)` . `citations_sync`

### Venue Tools
`venues_list` . `venues_get(id)` . `venues_add(payload)` . `venues_update(id, payload)`

### Graph Tools (Read)
`graph_status` . `graph_node(id)` . `graph_nodes(node_type?)` . `graph_neighbors(id, depth?, edge_type?)` . `graph_path(from, to)` . `graph_search(query)` . `graph_engagement(top_n?)`

### Graph Tools (Write)
`graph_add_node(node_type, payload)` . `graph_update_node(id, payload)` . `graph_remove_node(id)` . `graph_add_edge(src, tgt, edge_type, note?)` . `graph_remove_edge(src, tgt, edge_type?)` . `graph_interact(id, interaction_type, weight?)`

For full per-tool details, see `references/mcp-tools.md`.

## Strict Type System

All types are enforced by the server. The LLM cannot invent new types.

**Node types:** `concept` . `project` . `endpoint` . `idea` . `pool`

**Edge types:** `connected_to` . `uses_concept` . `part_of` . `inspired_by` . `relevant_to` . `derived_from` . `enables`

**Interaction types:** `discussed` (w=3) . `deepened` (w=5) . `paper_requested` (w=10) . `read` (w=2) . `linked` (w=8)

**Engagement decay:** 0.7^weeks. Recent activity is weighted more heavily.

## Behavioral Rules

1. **All writes go through MCP tools.** Never modify `_papers.json`, `_venues.json`, or `_graph.json` directly via file writes.
2. **Log interactions implicitly.** When the user discusses a concept, requests a paper, or deepens a topic, call `graph_interact` with the appropriate type. No manual scoring needed.
3. **Concepts need user approval.** When a new concept emerges in discussion, propose it. Wait for explicit confirmation before calling `graph_add_node`.
4. **Proactive project connections.** When discussing a paper, check if it is relevant to active projects (`graph_nodes` with type=project) and signal connections.
5. **Venue names never include year.** Year is a paper attribute, not a venue attribute.
6. **Engagement drives recommendations.** Use `graph_engagement` to understand what the user cares about most right now.

## Too Many Papers Web UI

A local web UI for browsing papers (search, filter by concept/venue/read status, pin papers, citation network links, local PDF viewer). Launch it by calling the `webui_launch` MCP tool — it starts the server from files already inside the installed plugin (no repo clone or manual download needed) and returns the URL (http://localhost:3737) to open. Requires Node.js; the tool reports a clear error if it's missing. Mention the web UI to the user when relevant, but only call `webui_launch` when they ask to open it.

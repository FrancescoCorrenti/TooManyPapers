#!/usr/bin/env python3
"""
MCP Server for Papers to Read — Knowledge Graph Research Assistant
Exposes papers_api.py functions as MCP tools.
Run: python _scripts/mcp_server.py
"""

from mcp.server.fastmcp import FastMCP
import io
import contextlib
import json
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Add script dir to path so we can import papers_api
sys.path.insert(0, str(Path(__file__).parent))
import papers_api

mcp = FastMCP("papers-research-assistant")

# server/_scripts/mcp_server.py -> plugin root -> webui/
PLUGIN_ROOT = Path(__file__).parent.parent.parent
WEBUI_DIR = PLUGIN_ROOT / "webui"
WEBUI_SERVER = WEBUI_DIR / "paper-library-server.js"
WEBUI_PORT = 3737


def _capture(func, args=None):
    """Run a papers_api command and capture its stdout output.

    papers_api's cmd_* functions are written for CLI use and call
    sys.exit(1) on validation errors. Left uncaught, that SystemExit
    propagates out of the tool call and kills the whole long-running MCP
    server process — turning one bad call (e.g. an unrecognized field)
    into a full server crash. Catch it here so a bad call just returns an
    error string and the server keeps running."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            func(args or [])
    except SystemExit:
        pass
    except Exception as e:
        buf.write(f"\nERROR: unexpected exception in '{func.__name__}': {e}")
    return buf.getvalue()


def _parse_list_arg(value: str) -> list:
    """Accepts a JSON array (of strings or objects) for bulk tools. Every
    bulk tool below takes its items this way instead of one call per item,
    so bulk-adding/deleting N things is one tool call, not N."""
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    return parsed


def _bulk(items: list, label_fn, call_fn) -> str:
    """Run call_fn over each item, collecting a per-item result plus a
    success/failure summary. A single bad item never aborts the rest."""
    results = []
    ok = fail = 0
    for i, item in enumerate(items):
        try:
            label = label_fn(item)
        except Exception:
            label = f"item #{i}"
        out = call_fn(item)
        failed = any(marker in out for marker in ("ERROR", "REJECTED", "not found"))
        ok += 0 if failed else 1
        fail += 1 if failed else 0
        results.append(f"--- {label} ---\n{out.strip()}")
    summary = f"Bulk result: {ok} succeeded, {fail} failed, out of {len(items)}."
    return summary + "\n\n" + "\n\n".join(results)


# =============================================================================
# Paper tools
# =============================================================================

@mcp.tool()
def papers_list() -> str:
    """List all papers in the catalog (ID, title, year, venue)."""
    return _capture(papers_api.cmd_list)


@mcp.tool()
def papers_get(id: str) -> str:
    """Get the full record for a paper by its ID (e.g. P001)."""
    return _capture(papers_api.cmd_get, [id])


@mcp.tool()
def papers_search(query: str) -> str:
    """Fuzzy search papers by title or author name."""
    return _capture(papers_api.cmd_search, [query])


@mcp.tool()
def papers_by_concept(concept_id: str) -> str:
    """List papers linked to a concept (e.g. C003)."""
    return _capture(papers_api.cmd_by_concept, [concept_id])


@mcp.tool()
def papers_by_author(author: str) -> str:
    """List papers whose author list contains the given surname."""
    return _capture(papers_api.cmd_by_author, [author])


@mcp.tool()
def papers_by_venue(venue_id: str) -> str:
    """List papers published in a venue (e.g. V001)."""
    return _capture(papers_api.cmd_by_venue, [venue_id])


@mcp.tool()
def papers_by_year(year: int) -> str:
    """List papers published in a given year."""
    return _capture(papers_api.cmd_by_year, [str(year)])


@mcp.tool()
def papers_outside() -> str:
    """List papers marked as 'outside the comfort zone'."""
    return _capture(papers_api.cmd_outside)


@mcp.tool()
def papers_hidden() -> str:
    """List hidden papers."""
    return _capture(papers_api.cmd_hidden)


@mcp.tool()
def papers_next_id() -> str:
    """Return the next available paper ID (e.g. P021)."""
    return _capture(papers_api.cmd_next_id)


@mcp.tool()
def papers_add(payload: str) -> str:
    """Add a new paper to the catalog. ID is assigned automatically.

    Args:
        payload: JSON string with required fields: title, authors, year,
            discovered, venue_id, venue_detail, source_verified, concepts,
            file, outside_zone, notes. Optional: url, hidden, cites, cited_by.
    """
    return _capture(papers_api.cmd_add_paper, [payload])


@mcp.tool()
def papers_update(id: str, payload: str) -> str:
    """Update fields on an existing paper (merge patch).

    Args:
        id: Paper ID (e.g. P004).
        payload: JSON string with fields to update (partial merge).
    """
    return _capture(papers_api.cmd_update_paper, [id, payload])


@mcp.tool()
def papers_check_duplicates(payload: str) -> str:
    """Check a list of candidate papers against the catalog for duplicates.
    Returns only non-duplicate candidates. Always call this BEFORE adding papers.

    Args:
        payload: JSON string — a list of candidates, or an object with a
            'results' key containing the list. Each candidate should have
            title, and optionally doi and arxiv_id.
    """
    return _capture(papers_api.cmd_check_duplicates, [payload])


@mcp.tool()
def papers_discover(query: str = "", concept_id: str = "", seed_paper_ids: list[str] | None = None,
                     providers: list[str] | None = None, year_from: int = 0, max_results: int = 10) -> str:
    """Search external providers (arXiv, Semantic Scholar, OpenAlex) for real
    papers matching a topic, and/or expand from the citations of papers
    already in the catalog. This is the ONLY sanctioned way to discover new
    papers — never use WebSearch or WebFetch to find papers. Every result
    comes from a real API response, already deduplicated across providers
    and against your existing catalog, ready to be checked and added via
    papers_add.

    Args:
        query: Topic/keywords to search for. Can be omitted if concept_id or
            seed_paper_ids alone provide enough context.
        concept_id: Optional graph concept ID (e.g. C003) — its name, area,
            and description are appended to the query automatically.
        seed_paper_ids: Optional list of paper IDs already in the catalog;
            their references (via Semantic Scholar) are pulled in as
            additional candidates — this is how citation-based discovery
            fits into the same flow instead of being a separate step.
        providers: Subset of "arxiv", "semantic_scholar", "openalex" to use
            (default: all three).
        year_from: Optional minimum publication year filter (0 = no filter).
        max_results: Max results per provider before merging/dedup (1-50,
            default 10).
    """
    payload = {"query": query, "max_results": max_results}
    if concept_id:
        payload["concept_id"] = concept_id
    if seed_paper_ids:
        payload["seed_paper_ids"] = seed_paper_ids
    if providers:
        payload["providers"] = providers
    if year_from:
        payload["year_from"] = year_from
    return _capture(papers_api.cmd_papers_discover, [json.dumps(payload)])


@mcp.tool()
def papers_hide(id: str) -> str:
    """Hide a paper (sets hidden=true). Hidden papers are excluded from
    standard filters and the daily briefing."""
    return _capture(papers_api.cmd_hide, [id])


@mcp.tool()
def papers_unhide(id: str) -> str:
    """Unhide a previously hidden paper (sets hidden=false)."""
    return _capture(papers_api.cmd_unhide, [id])


@mcp.tool()
def papers_delete(id: str) -> str:
    """Permanently delete a paper from the catalog. This is NOT the same as
    papers_hide — the record is actually removed, not just flagged. Also
    scrubs the deleted ID out of every other paper's cites/cited_by lists.
    Ask the user to confirm before calling this; it cannot be undone."""
    return _capture(papers_api.cmd_delete_paper, [id])


@mcp.tool()
def papers_add_bulk(payloads: str) -> str:
    """Add several papers in one call. Each is validated and saved
    independently (same rules as papers_add), so one bad entry doesn't block
    the rest.

    Args:
        payloads: JSON array of paper payload objects, same shape as the
            'payload' argument of papers_add.
    """
    items = _parse_list_arg(payloads)
    return _bulk(items, lambda p: p.get("title", "?"),
                 lambda p: _capture(papers_api.cmd_add_paper, [json.dumps(p)]))


@mcp.tool()
def papers_delete_bulk(ids: str) -> str:
    """Permanently delete several papers in one call. Ask the user to
    confirm before calling this; it cannot be undone.

    Args:
        ids: JSON array of paper IDs, e.g. '["P001", "P004"]'.
    """
    items = _parse_list_arg(ids)
    return _bulk(items, lambda pid: pid,
                 lambda pid: _capture(papers_api.cmd_delete_paper, [pid]))


@mcp.tool()
def papers_export(format: str = "bibtex", ids: str = "") -> str:
    """Export papers as a citation file (currently BibTeX) with an output
    validation report. Returns the file text; any problems (missing year,
    no DOI/URL, unverified metadata, duplicate cite keys) are appended as
    trailing '% WARN:' comment lines — surface those to the user rather than
    handing over a silently-incomplete file.

    A fresh library.bib for the whole (non-hidden) library is also kept
    up to date automatically at ~/.too-many-papers/exports/library.bib; this
    tool is for on-demand or selective exports.

    Args:
        format: Export format. Currently only "bibtex".
        ids: Optional comma-separated paper IDs to export just a subset
            (e.g. "P001,P004"). Empty exports the whole non-hidden library.
    """
    args = ["--format", format, "--report"]
    if ids.strip():
        args += ["--ids", ids.strip()]
    return _capture(papers_api.cmd_export, args)


# =============================================================================
# Daily briefing tools
# =============================================================================

@mcp.tool()
def briefing_generate(date: str = "", concepts: int = 3, per_concept: int = 6,
                      year_from: int = 0) -> str:
    """Generate today's paper briefing and save it as a Markdown digest under
    ~/.too-many-papers/briefings/<date>.md, then return it. READ-ONLY: it
    discovers fresh candidate papers for the user's most-engaged concepts but
    does NOT add anything to the catalog. Present the digest and ask the user
    which papers they want to add (then use papers_add for those).

    This is the single tool a scheduled briefing routine should call. Note:
    scheduling only works in Cowork; Claude Code's scheduled tasks can't reach
    this local MCP server.

    Args:
        date: Briefing date as YYYY-MM-DD. Empty means today.
        concepts: How many top concepts to cover (default 3).
        per_concept: Max candidate papers per concept (default 6).
        year_from: Minimum publication year. 0 means the current year.
    """
    args = []
    if date.strip():
        args += ["--date", date.strip()]
    args += ["--concepts", str(concepts), "--per-concept", str(per_concept)]
    if year_from:
        args += ["--year-from", str(year_from)]
    return _capture(papers_api.cmd_briefing, args)


@mcp.tool()
def briefing_list() -> str:
    """List the dates of saved briefings, newest first."""
    return _capture(papers_api.cmd_briefing_list)


@mcp.tool()
def briefing_get(date: str = "") -> str:
    """Return a saved briefing digest.

    Args:
        date: YYYY-MM-DD. Empty returns the most recent briefing.
    """
    return _capture(papers_api.cmd_briefing_get, [date.strip()] if date.strip() else [])


# =============================================================================
# Citation tools
# =============================================================================

@mcp.tool()
def citations_get(id: str) -> str:
    """Fetch real citations from Semantic Scholar for a paper. Read-only —
    does NOT save anything to disk."""
    return _capture(papers_api.cmd_get_citations, [id])


@mcp.tool()
def citations_apply(id: str) -> str:
    """Fetch citations from Semantic Scholar and save the links to _papers.json."""
    return _capture(papers_api.cmd_apply_citations, [id])


@mcp.tool()
def citations_sync() -> str:
    """Run apply-citations on ALL papers in the catalog. May take a while
    due to Semantic Scholar rate limits."""
    return _capture(papers_api.cmd_sync_citations)


# =============================================================================
# PDF tools
# =============================================================================

@mcp.tool()
def papers_fetch_pdf(id: str) -> str:
    """Resolve and download an open-access PDF for a single paper (tries
    arXiv, then PMC, then bioRxiv, then Semantic Scholar, then Unpaywall, in
    that order — no scraping, no paywall bypass). On success, sets `file`/`pdf_source` on
    the paper. On failure, sets `pdf_status` to "unavailable" (no
    open-access source found) or "error: <reason>" (a source was found but
    download/validation failed) instead — never invents a `file` value."""
    return _capture(papers_api.cmd_fetch_pdf, [id])


@mcp.tool()
def papers_sync_pdfs() -> str:
    """Run fetch-pdf on every paper in the catalog. Skips papers that
    already have a PDF on disk (idempotent). May take a while due to
    Semantic Scholar/Unpaywall rate limits."""
    return _capture(papers_api.cmd_sync_pdfs)


# =============================================================================
# Venue tools
# =============================================================================

@mcp.tool()
def venues_list() -> str:
    """List all venues (ID, name, type)."""
    return _capture(papers_api.cmd_venue_list)


@mcp.tool()
def venues_get(id: str) -> str:
    """Get the full record for a venue by its ID (e.g. V003)."""
    return _capture(papers_api.cmd_venue_get, [id])


@mcp.tool()
def venues_add(payload: str) -> str:
    """Add a new venue. ID is assigned automatically.

    Args:
        payload: JSON string with required fields: name, type.
            Optional: publisher, url, open_access, peer_reviewed, metrics, notes.
            Note: venue name must NOT include the year.
    """
    return _capture(papers_api.cmd_add_venue, [payload])


@mcp.tool()
def venues_update(id: str, payload: str) -> str:
    """Update fields on an existing venue (merge patch).

    Args:
        id: Venue ID (e.g. V003).
        payload: JSON string with fields to update.
    """
    return _capture(papers_api.cmd_update_venue, [id, payload])


@mcp.tool()
def venues_delete(id: str, force: bool = False) -> str:
    """Permanently delete a venue. Blocked if any papers still reference it
    (reassign those papers' venue_id first) unless force=True, in which case
    those papers are left pointing at a missing venue_id. Ask the user to
    confirm before calling this; it cannot be undone.

    Args:
        id: Venue ID (e.g. V003).
        force: Delete even if papers still reference this venue.
    """
    args = [id, "force"] if force else [id]
    return _capture(papers_api.cmd_delete_venue, args)


@mcp.tool()
def venues_add_bulk(payloads: str) -> str:
    """Add several venues in one call. Each is validated and saved
    independently, so one bad entry doesn't block the rest.

    Args:
        payloads: JSON array of venue payload objects, same shape as the
            'payload' argument of venues_add.
    """
    items = _parse_list_arg(payloads)
    return _bulk(items, lambda v: v.get("name", "?"),
                 lambda v: _capture(papers_api.cmd_add_venue, [json.dumps(v)]))


@mcp.tool()
def venues_delete_bulk(ids: str, force: bool = False) -> str:
    """Permanently delete several venues in one call. Ask the user to
    confirm before calling this; it cannot be undone.

    Args:
        ids: JSON array of venue IDs, e.g. '["V001", "V002"]'.
        force: Delete even if papers still reference a venue (same as
            venues_delete's force flag, applied to every ID).
    """
    items = _parse_list_arg(ids)
    args_fn = (lambda vid: [vid, "force"]) if force else (lambda vid: [vid])
    return _bulk(items, lambda vid: vid,
                 lambda vid: _capture(papers_api.cmd_delete_venue, args_fn(vid)))


# =============================================================================
# Graph tools
# =============================================================================

@mcp.tool()
def graph_status() -> str:
    """Overview of the knowledge graph: node counts by type, edge count,
    interaction count, and latest interaction date."""
    return _capture(papers_api.cmd_graph_status)


@mcp.tool()
def graph_node(id: str) -> str:
    """Get a single node with full context: all fields, connected edges,
    and recent interactions."""
    return _capture(papers_api.cmd_graph_node, [id])


@mcp.tool()
def graph_nodes(node_type: str = "") -> str:
    """List all nodes in the knowledge graph, optionally filtered by type.

    Args:
        node_type: If provided, filter by type. Must be one of:
            concept, project, endpoint, idea, waypoint.
            Leave empty to list all nodes.
    """
    args = []
    if node_type:
        args = ["--type", node_type]
    return _capture(papers_api.cmd_graph_nodes, args)


def _add_node(node_type: str, payload: dict) -> str:
    return _capture(papers_api.cmd_graph_add_node, [node_type, json.dumps(payload)])


@mcp.tool()
def graph_add_concept(name: str, area: str, description: str = "") -> str:
    """Add a concept node to the knowledge graph — a research area the user
    cares about (e.g. "Brain Lesion Segmentation").

    Args:
        name: Concept name.
        area: Broader field/area this concept belongs to (e.g. "Medical Imaging / Deep Learning").
        description: Optional one-sentence description.
    """
    payload = {"name": name, "area": area}
    if description:
        payload["description"] = description
    return _add_node("concept", payload)


@mcp.tool()
def graph_add_project(name: str, status: str, description: str = "") -> str:
    """Add a project node — an active research project with a goal. A project
    connects only to concepts, ideas, waypoints, and endpoints — never
    directly to a paper.

    Args:
        name: Project name.
        status: Free-text status, e.g. ideation, literature-review, active, writing.
        description: Optional one-sentence goal/summary (max 200 characters).
            Use this field for anything like a project's goal or description
            — there is no separate "goal" field.
    """
    payload = {"name": name, "status": status}
    if description:
        payload["description"] = description
    return _add_node("project", payload)


@mcp.tool()
def graph_add_endpoint(name: str, status: str = "pending", description: str = "") -> str:
    """Add an endpoint node — a project's goal/milestone that a chain of
    waypoints (see graph_add_waypoint) leads to via "leads_to" edges.

    Args:
        name: Endpoint name.
        status: One of "pending" (default), "reached", or "failed".
        description: Optional one-sentence description.
    """
    payload = {"name": name, "status": status}
    if description:
        payload["description"] = description
    return _add_node("endpoint", payload)


@mcp.tool()
def graph_add_idea(name: str, status: str, created: str, description: str = "", source: str = "") -> str:
    """Add an idea node — a concrete idea connected to a project.

    Args:
        name: Idea name.
        status: Free-text status.
        created: Creation date, e.g. "2026-07-02".
        description: Optional one-sentence description.
        source: Optional origin of the idea (e.g. a paper ID).
    """
    payload = {"name": name, "status": status, "created": created}
    if description:
        payload["description"] = description
    if source:
        payload["source"] = source
    return _add_node("idea", payload)


@mcp.tool()
def graph_add_waypoint(name: str, description: str = "", status: str = "pending") -> str:
    """Add a waypoint node — an intermediate node in a project's chain toward
    an endpoint. Connect it to the chain with "leads_to" edges (waypoint ->
    waypoint, or waypoint -> endpoint for the last one). A waypoint may have
    at most one outgoing "leads_to" edge (enforced by the server — add a
    second one and it's rejected); incoming edges are a soft convention of
    one, but an endpoint itself may receive several independent chains.

    Args:
        name: Waypoint name.
        description: Optional one-sentence description.
        status: One of "pending" (default), "reached", or "failed".
    """
    payload = {"name": name, "status": status}
    if description:
        payload["description"] = description
    return _add_node("waypoint", payload)


@mcp.tool()
def graph_add_note(name: str, created: str, quote: str = "", text: str = "", page: int = 0) -> str:
    """Add a note node — a reading annotation, typically tied to a paper via
    an `annotates` edge (use graph_add_edge with type "annotates"). Usually
    created from the web UI's select-to-note flow, but the AI can add one
    too when the user dictates a note during conversation.

    Args:
        name: Short label for the note (e.g. a truncated excerpt).
        created: Creation date, e.g. "2026-07-02".
        quote: Optional verbatim excerpt the note is about.
        text: Optional free-text comment on the excerpt.
        page: Optional PDF page number the note refers to (0 = none).
    """
    payload = {"name": name, "created": created}
    if quote:
        payload["quote"] = quote
    if text:
        payload["text"] = text
    if page:
        payload["page"] = page
    return _add_node("note", payload)


@mcp.tool()
def graph_update_node(id: str, payload: str) -> str:
    """Update fields on an existing graph node (merge patch).

    Args:
        id: Node ID (e.g. C003, PROJ-FOO).
        payload: JSON string with fields to update.
    """
    return _capture(papers_api.cmd_graph_update_node, [id, payload])


@mcp.tool()
def graph_remove_node(id: str) -> str:
    """Remove a node and all its edges from the knowledge graph."""
    return _capture(papers_api.cmd_graph_remove_node, [id])


@mcp.tool()
def graph_add_nodes_bulk(nodes: str) -> str:
    """Add several nodes (any mix of types) to the knowledge graph in one
    call. Each is validated and saved independently, so one bad entry
    doesn't block the rest.

    Args:
        nodes: JSON array of objects, each with a "type" field (concept,
            project, endpoint, idea, waypoint, or note) plus that type's fields
            — same shape as graph_add_concept/graph_add_project/etc, e.g.
            '[{"type": "concept", "name": "X", "area": "Y"}, ...]'.
    """
    items = _parse_list_arg(nodes)

    def call(node):
        node = dict(node)
        node_type = node.pop("type", "")
        return _capture(papers_api.cmd_graph_add_node, [node_type, json.dumps(node)])

    return _bulk(items, lambda n: n.get("name", "?"), call)


@mcp.tool()
def graph_remove_nodes_bulk(ids: str) -> str:
    """Remove several nodes (and all their edges) from the knowledge graph
    in one call. Ask the user to confirm before calling this; it cannot be
    undone.

    Args:
        ids: JSON array of node IDs, e.g. '["C003", "PROJ-FOO"]'.
    """
    items = _parse_list_arg(ids)
    return _bulk(items, lambda nid: nid,
                 lambda nid: _capture(papers_api.cmd_graph_remove_node, [nid]))


@mcp.tool()
def graph_add_edge(src: str, tgt: str, edge_type: str, note: str = "") -> str:
    """Add an edge between two nodes in the knowledge graph.

    Args:
        src: Source node ID.
        tgt: Target node ID.
        edge_type: Must be one of: connected_to, uses_concept, part_of,
            inspired_by, relevant_to, derived_from, enables, leads_to. A
            project can only connect to concept/idea/waypoint/endpoint nodes
            (never a paper). leads_to must go waypoint -> waypoint or
            waypoint -> endpoint (the project's chain toward its goal); a
            waypoint can have at most one outgoing leads_to edge.
        note: Optional free-text annotation for the edge.
    """
    args = [src, tgt, edge_type]
    if note:
        args.append(note)
    return _capture(papers_api.cmd_graph_add_edge, args)


@mcp.tool()
def graph_remove_edge(src: str, tgt: str, edge_type: str = "") -> str:
    """Remove edge(s) between two nodes, optionally filtered by type.

    Args:
        src: Source node ID.
        tgt: Target node ID.
        edge_type: If provided, only remove edges of this type.
    """
    args = [src, tgt]
    if edge_type:
        args.extend(["--type", edge_type])
    return _capture(papers_api.cmd_graph_remove_edge, args)


@mcp.tool()
def graph_add_edges_bulk(edges: str) -> str:
    """Add several edges in one call. Each is validated and saved
    independently, so one bad entry doesn't block the rest.

    Args:
        edges: JSON array of objects with src, tgt, type, and optional note
            — same fields as graph_add_edge, e.g.
            '[{"src": "P001", "tgt": "C003", "type": "uses_concept"}, ...]'.
    """
    items = _parse_list_arg(edges)

    def call(e):
        args = [e.get("src", ""), e.get("tgt", ""), e.get("type", "")]
        if e.get("note"):
            args.append(e["note"])
        return _capture(papers_api.cmd_graph_add_edge, args)

    return _bulk(items, lambda e: f"{e.get('src')} -> {e.get('tgt')} [{e.get('type')}]", call)


@mcp.tool()
def graph_remove_edges_bulk(edges: str) -> str:
    """Remove several edges in one call.

    Args:
        edges: JSON array of objects with src, tgt, and optional type
            (omit type to remove all edges between that src/tgt), e.g.
            '[{"src": "P001", "tgt": "C003"}, {"src": "P002", "tgt": "C001", "type": "relevant_to"}]'.
    """
    items = _parse_list_arg(edges)

    def call(e):
        args = [e.get("src", ""), e.get("tgt", "")]
        if e.get("type"):
            args.extend(["--type", e["type"]])
        return _capture(papers_api.cmd_graph_remove_edge, args)

    return _bulk(items, lambda e: f"{e.get('src')} -> {e.get('tgt')}", call)


@mcp.tool()
def graph_neighbors(id: str, depth: int = 1, edge_type: str = "") -> str:
    """BFS traversal from a node, returning all reachable neighbors.

    Args:
        id: Starting node ID.
        depth: How many hops to traverse (1-3, default 1).
        edge_type: If provided, only follow edges of this type.
    """
    args = [id, "--depth", str(depth)]
    if edge_type:
        args.extend(["--edge-type", edge_type])
    return _capture(papers_api.cmd_graph_neighbors, args)


@mcp.tool()
def graph_path(from_id: str, to_id: str) -> str:
    """Find the shortest path between two nodes (BFS, max depth 6)."""
    return _capture(papers_api.cmd_graph_path, [from_id, to_id])


@mcp.tool()
def graph_interact(id: str, interaction_type: str, weight: int = 0) -> str:
    """Log an interaction with a node (updates engagement scores).

    Args:
        id: Node ID to interact with.
        interaction_type: Must be one of: discussed, deepened,
            paper_requested, read, linked
        weight: Override the default weight for this interaction type.
            If 0, uses the default (discussed=3, deepened=5,
            paper_requested=10, read=2, linked=8).

    Note: you rarely need "linked" here. graph_add_edge already logs it
    automatically when it creates a relevant_to or uses_concept edge, since
    that IS the linking event. Only use "linked" manually for edge types
    graph_add_edge doesn't auto-log (e.g. inspired_by, derived_from).
    """
    args = [id, interaction_type]
    if weight:
        args.extend(["--weight", str(weight)])
    return _capture(papers_api.cmd_graph_interact, args)


@mcp.tool()
def graph_engagement(top_n: int = 10) -> str:
    """Compute engagement scores for all nodes using exponential decay
    (decay factor 0.7 per week). Returns the top N nodes ranked by score.

    Args:
        top_n: Number of top nodes to return (default 10).
    """
    return _capture(papers_api.cmd_graph_engagement, ["--top", str(top_n)])


@mcp.tool()
def graph_search(query: str) -> str:
    """Full-text search across graph nodes and papers."""
    return _capture(papers_api.cmd_graph_search, [query])


@mcp.tool()
def graph_lint(stale_days: int = 90, quiet_days: int = 45, fix: bool = False) -> str:
    """Health-check the graph and paper catalog for hygiene issues. Read-only
    by default — reports problems, never fixes them automatically — except
    with fix=True, which additionally removes nodes/edges whose type isn't
    in the graph's official type list (GRAPH_NODE_TYPES/GRAPH_EDGE_TYPES),
    the same list graph_add_node/graph_add_edge validate against. Checks:
    orphan nodes (no edges), projects with no papers linked, papers with no
    concept/edge, ideas untouched for stale_days+ and not closed, papers
    pointing at a missing venue, cites/cited_by pointing at a missing paper,
    concepts with no interaction in quiet_days+, and nodes/edges with a
    non-official type. Run this occasionally, or when the user asks to
    check the graph's health — never automatically without being asked, and
    never pass fix=True without the user asking for the cleanup.

    Args:
        stale_days: Age threshold (days) for flagging an open idea as stale (default 90).
        quiet_days: Days without interaction before a concept is flagged as quiet (default 45).
        fix: If True, remove nodes/edges with a non-official type (default False).
    """
    fix_args = ["--fix"] if fix else []
    return _capture(papers_api.cmd_graph_lint,
                     ["--stale-days", str(stale_days), "--quiet-days", str(quiet_days)] + fix_args)


# =============================================================================
# Paper Library web UI
# =============================================================================

def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_process_on_port(port: int) -> bool:
    """Finds and kills whatever process is listening on `port`, so
    webui_launch can restart a stale/stuck server instead of just reporting
    it's already running. Shells out to platform-native tools (no extra
    dependency like psutil) — netstat/taskkill on Windows, lsof/kill
    elsewhere. Returns True if a process was found and killed."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            ).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "TCP" and f":{port}" in parts[1] \
                        and parts[3] == "LISTENING":
                    pids.add(parts[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5)
            return bool(pids)
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=5
            ).stdout
            pids = [p for p in out.split() if p]
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
            return bool(pids)
    except Exception:
        return False


@mcp.tool()
def webui_launch() -> str:
    """Start the local Too Many Papers web UI (search, filters, PDF viewer,
    citation graph) and return its URL. Runs entirely from files already
    inside the installed plugin — no separate download or repo clone
    needed. Requires Node.js. If the server is already running, it is
    killed and restarted fresh (picks up any updated web UI files and
    clears a stuck/stale instance) rather than left as-is."""
    if not WEBUI_SERVER.exists():
        return f"Error: web UI files not found at {WEBUI_SERVER}."

    if not shutil.which("node"):
        return (
            "Node.js is not installed or not on PATH. Install it from "
            "https://nodejs.org, then try again."
        )

    restarted = False
    if _port_in_use(WEBUI_PORT):
        if not _kill_process_on_port(WEBUI_PORT):
            return (
                f"Too Many Papers is already running at http://localhost:{WEBUI_PORT} "
                "but the existing process could not be found/killed to restart it "
                "(close it manually and try again)."
            )
        restarted = True
        for _ in range(20):
            if not _port_in_use(WEBUI_PORT):
                break
            time.sleep(0.2)

    import os

    subprocess.Popen(
        ["node", str(WEBUI_SERVER)],
        cwd=str(WEBUI_DIR),
        env={
            **os.environ,
            "PORT": str(WEBUI_PORT),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    action = "restarted" if restarted else "starting"
    return (
        f"Too Many Papers {action} at http://localhost:{WEBUI_PORT} "
        "— open that URL in your browser. "
        f"Data directory: {papers_api.DATA_DIR}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")

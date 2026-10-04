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

mcp = FastMCP("papers-research-assistant", instructions=(
    "Projects connect only to papers. For every association you MUST read the paper, "
    "analyze its relevance to the project, and call graph_link_project_paper with a "
    "grounded summary of ONLY relevant findings, evidence and limitations. First call "
    "graph_project_context for the context_token and existing ideas. Use graph_review_queue "
    "when maintaining projects: migrated ideas and stale analyses require source review. "
    "Do not invent links for legacy ideas without an identified paper. Refresh analyses "
    "with graph_link_project_paper; use legacy_idea_ids to record incorporated legacy content. "
    "Project development and task planning remain under the user's control."
))

# server/_scripts/mcp_server.py -> plugin root -> webui/
PLUGIN_ROOT = Path(__file__).parent.parent.parent
WEBUI_DIR = PLUGIN_ROOT / "webui"
WEBUI_SERVER = WEBUI_DIR / "paper-library-server.js"
WEBUI_PORT = 3737


# Seconds one tool call may spend on the network before giving up cleanly.
# Tools run one at a time, so an unbounded call would freeze every call after it.
CALL_BUDGET = 45
ADD_BUDGET = 30     # per paper: citation links + PDF fetch
SYNC_BUDGET = 100   # sync tools stop here and resume on the next call


def _capture(func, args=None, budget=CALL_BUDGET):
    """Run a papers_api command and capture its stdout output.

    papers_api's cmd_* functions are written for CLI use and call
    sys.exit(1) on validation errors. Left uncaught, that SystemExit
    propagates out of the tool call and kills the whole long-running MCP
    server process — turning one bad call (e.g. an unrecognized field)
    into a full server crash. Catch it here so a bad call just returns an
    error string and the server keeps running."""
    buf = io.StringIO()
    try:
        lock = papers_api.graph_command_lock() if func.__name__.startswith("cmd_graph_") or func.__name__ == "cmd_delete_paper" else contextlib.nullcontext()
        with lock, papers_api.time_budget(budget), contextlib.redirect_stdout(buf):
            func(args or [])
    except SystemExit:
        pass
    except Exception as e:
        buf.write(f"\nERROR: unexpected exception in '{func.__name__}': {e}")
    return buf.getvalue()


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

def _one_or_many(value: str) -> list:
    """Parse a JSON object/string or an array of them into a list."""
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else [parsed]


@mcp.tool()
def papers_get(id: str) -> str:
    """Get the full record for a paper by its ID (e.g. P001)."""
    return _capture(papers_api.cmd_get, [id])


@mcp.tool()
def papers_find(query: str = "", author: str = "", year: int = 0, venue: str = "",
                concept_id: str = "", hidden: str = "false") -> str:
    """Find papers in the catalog. Every filter is optional and they combine
    with AND; with no filter it lists the whole visible catalog.

    Args:
        query: Substring of the title or of any author name.
        author: Substring of an author name.
        year: Publication year (0 = any).
        venue: Substring of the venue name or venue detail.
        concept_id: Concept the paper is tagged with (e.g. C003).
        hidden: "false" (default) visible papers only, "true" hidden only, "any" both.
    """
    flags = {"false": False, "true": True, "any": "any"}
    payload = {"query": query, "author": author, "year": year, "venue": venue,
               "concept_id": concept_id, "hidden": flags.get(hidden.lower(), False)}
    return _capture(papers_api.cmd_find, [json.dumps(payload)])


@mcp.tool()
def papers_add(payload: str) -> str:
    """Add one paper, or several in one call. IDs are assigned automatically;
    citation links and the open-access PDF are resolved on add.

    Args:
        payload: JSON object, or JSON array of objects. Required: title,
            authors (complete, verbatim), year, source_verified (URL of the
            primary source you read this session). Optional: venue (name,
            without the year — created if new), venue_type, venue_detail,
            url, abstract, concepts (list of concept IDs), notes,
            outside_zone, hidden.
    """
    items = _one_or_many(payload)
    if len(items) == 1:
        return _capture(papers_api.cmd_add_paper, [json.dumps(items[0])], ADD_BUDGET)
    return _bulk(items, lambda p: p.get("title", "?"),
                 lambda p: _capture(papers_api.cmd_add_paper, [json.dumps(p)], ADD_BUDGET))


@mcp.tool()
def papers_update(id: str, payload: str) -> str:
    """Update fields on an existing paper (merge patch). Hide a paper with
    {"hidden": true}; change its venue by name with {"venue": "..."}.

    Args:
        id: Paper ID (e.g. P004).
        payload: JSON string with fields to update (partial merge).
    """
    return _capture(papers_api.cmd_update_paper, [id, payload])


@mcp.tool()
def papers_delete(ids: str) -> str:
    """Permanently delete one or more papers and scrub them from every other
    paper's cites/cited_by. To keep a paper but take it out of view, use
    papers_update with {"hidden": true} instead. Ask the user to confirm
    before calling this; it cannot be undone.

    Args:
        ids: A paper ID (e.g. "P001") or a JSON array of IDs.
    """
    items = _one_or_many(ids) if ids.strip().startswith(("[", '"')) else [ids.strip()]
    if len(items) == 1:
        return _capture(papers_api.cmd_delete_paper, [items[0]])
    return _bulk(items, lambda pid: pid,
                 lambda pid: _capture(papers_api.cmd_delete_paper, [pid]))


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
            additional candidates.
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
    """Run apply-citations over the catalog, least recently checked first.
    Stops after about 100 seconds and says how many are left; call it again
    to continue."""
    return _capture(papers_api.cmd_sync_citations, budget=SYNC_BUDGET)


# =============================================================================
# PDF tools
# =============================================================================

@mcp.tool()
def papers_get_pdf(id: str) -> str:
    """Get the absolute, on-disk path to a paper's PDF, fetching it first
    from open-access sources (arXiv, PMC, bioRxiv, Semantic Scholar,
    Unpaywall — no scraping, no paywall bypass) only if it isn't already on
    disk. If no source exists, `pdf_status` records why; a `file` value is
    never invented."""
    return _capture(papers_api.cmd_get_pdf, [id])


@mcp.tool()
def papers_get_pdf_markdown(id: str) -> str:
    """Get a paper's PDF converted to markdown text — the way to actually
    read a paper. Fetches the PDF first only if it isn't already on disk.
    Requires the optional `markitdown` package (`pip install
    too-many-papers[markdown]`); without it, returns a message saying so —
    fall back to papers_get_pdf in that case. Conversion is cached."""
    return _capture(papers_api.cmd_get_pdf_markdown, [id])


@mcp.tool()
def papers_sync_pdfs() -> str:
    """Fetch the open-access PDF for every paper that doesn't have one on
    disk yet, least recently tried first. Stops after about 100 seconds and
    says how many are left; call it again to continue."""
    return _capture(papers_api.cmd_sync_pdfs, budget=SYNC_BUDGET)


# =============================================================================
# Project / paper analysis tools
# =============================================================================

@mcp.tool()
def graph_project_context(project_id: str, paper_id: str = "") -> str:
    """Get a project's linked papers and relevance ideas (including stale/detached and legacy ideas).
    Supply paper_id before linking/refreshing: returns the paper record and context_token.
    Read the actual paper with papers_get_pdf_markdown where available; never invent findings.
    """
    return _capture(papers_api.cmd_graph_project_context, [project_id, paper_id])


@mcp.tool()
def graph_link_project_paper(project_id: str, paper_id: str, name: str, relevance: str,
                             relevant_summary: str, evidence: str, limitations: str,
                             context_token: str, legacy_idea_ids: list[str] | None = None) -> str:
    """Atomically link a project to a paper AND create its required relevance idea.
    The calling LLM MUST first read/analyze the paper against graph_project_context.
    relevance explains why it matters for THIS project; relevant_summary summarizes ONLY
    useful passages/findings, evidence gives section/page references, limitations states
    caveats and source coverage (e.g. abstract only). Never present unread material as read.
    Use the context_token from graph_project_context. A repeated pair refreshes the same
    idea and preserves analysis history. legacy_idea_ids records migrated ideas incorporated.
    This is the only way to link a project to a paper.
    """
    payload = dict(name=name, relevance=relevance, relevant_summary=relevant_summary,
                   evidence=evidence, limitations=limitations, context_token=context_token,
                   legacy_idea_ids=legacy_idea_ids or [])
    return _capture(papers_api.cmd_graph_link_project_paper, [project_id, paper_id, json.dumps(payload)])


@mcp.tool()
def graph_review_queue() -> str:
    """Return migrated ideas needing grounded analysis and stale project/paper analyses.
    Never discard legacy text or guess a paper. Inspect candidate_paper_ids and project_ids,
    read sources, then graph_link_project_paper with legacy_idea_ids. Unresolved ideas stay
    visible. Stale means project name/description or paper content/source changed, or the
    analysis was manually edited; age or reading activity alone does not make it stale.
    """
    return _capture(papers_api.cmd_graph_review_queue)


# =============================================================================
# Graph tools — concepts and projects
# =============================================================================

@mcp.tool()
def graph_status() -> str:
    """Overview: paper count, graph node counts by type, edge count."""
    return _capture(papers_api.cmd_graph_status)


@mcp.tool()
def graph_node(id: str) -> str:
    """Get a single node with all its fields and connected edges."""
    return _capture(papers_api.cmd_graph_node, [id])


@mcp.tool()
def graph_nodes(node_type: str = "") -> str:
    """List graph nodes, optionally filtered by type: concept, project,
    idea, or note. Leave empty to list all."""
    args = ["--type", node_type] if node_type else []
    return _capture(papers_api.cmd_graph_nodes, args)


@mcp.tool()
def graph_search(query: str) -> str:
    """Full-text search across graph nodes and papers."""
    return _capture(papers_api.cmd_graph_search, [query])


def _add_node(node_type: str, payload: dict) -> str:
    return _capture(papers_api.cmd_graph_add_node, [node_type, json.dumps(payload)])


@mcp.tool()
def graph_add_concept(name: str, area: str, description: str = "") -> str:
    """Add a concept — a research area the user keeps an eye on (e.g.
    "Brain Lesion Segmentation"). Concepts seed papers_discover and tag
    papers via the paper's `concepts` field.

    Args:
        name: Concept name.
        area: Broader field (e.g. "Medical Imaging / Deep Learning").
        description: Optional one sentence; used as discovery context.
    """
    payload = {"name": name, "area": area}
    if description:
        payload["description"] = description
    return _add_node("concept", payload)


@mcp.tool()
def graph_add_project(name: str, status: str, description: str = "") -> str:
    """Add a project — something the user is actively working on. A project
    connects only to papers, through graph_link_project_paper.

    Args:
        name: Project name.
        status: Free-text status, e.g. ideation, literature-review, active, writing.
        description: Optional one-sentence goal (max 200 characters). Changing
            it later marks the project's paper analyses stale.
    """
    payload = {"name": name, "status": status}
    if description:
        payload["description"] = description
    return _add_node("project", payload)


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
    """Remove a node and all its edges. Ask the user to confirm first."""
    return _capture(papers_api.cmd_graph_remove_node, [id])


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

    process = subprocess.Popen(
        ["node", str(WEBUI_SERVER)],
        cwd=str(WEBUI_DIR),
        env={
            **os.environ,
            "PORT": str(WEBUI_PORT),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    for _ in range(50):
        if process.poll() is not None:
            return f"Error: web UI exited with code {process.returncode} before becoming ready."
        if _port_in_use(WEBUI_PORT):
            break
        time.sleep(0.1)
    else:
        return "Error: web UI did not become ready within 5 seconds."

    action = "restarted" if restarted else "running"
    return (
        f"Too Many Papers {action} at http://localhost:{WEBUI_PORT} "
        "— open that URL in your browser. "
        f"Data directory: {papers_api.DATA_DIR}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")

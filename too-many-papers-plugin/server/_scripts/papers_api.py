#!/usr/bin/env python3
"""
papers_api.py — Local API for the Papers to Read system
=========================================================
Single interface for reading and writing _papers.json, _venues.json and _graph.json.
Designed to be called by Claude via bash.

USAGE
-----
  python _scripts/papers_api.py <command> [arguments]

SANDBOX NOTE
------------
  In the Cowork Linux sandbox, .py files on a mounted filesystem do not produce
  stdout when invoked as a direct subprocess. Use instead:

    python3 << 'EOF'
    import sys; sys.argv = ['papers_api.py', 'list']
    exec(open('/absolute/path/_scripts/papers_api.py').read()...)
    EOF

  Or import the module directly with importlib (see _CLAUDE_INSTRUCTIONS.md).

PAPER COMMANDS — READ
---------------------
  list                          -> all papers (ID, title, year, venue)
  get <ID>                      -> full record of a paper (e.g. P001)
  search <text>                 -> fuzzy search on title and authors
  by-concept <CID>              -> papers linked to a concept (e.g. C003)
  by-author <surname>           -> papers with author whose surname contains <surname>
  by-venue <VID>                -> papers published in a venue (e.g. V001)
  by-year <year>                -> papers published in a year
  outside                       -> only "outside the comfort zone" papers
  hidden                        -> only hidden papers
  next-id                       -> next available paper ID (e.g. P021)

PAPER COMMANDS — WRITE
-----------------------
  add-paper <json|@file>        -> adds a paper (ID assigned automatically)
                                  Accepts inline JSON or @path/file.json to
                                  avoid shell quoting issues with apostrophes/accents.
  update-paper <ID> <json|@file> -> updates fields of an existing paper (partial merge)
  hide <ID>                     -> hides a paper (field hidden = true)
  unhide <ID>                   -> restores a hidden paper (field hidden = false)
  delete-paper <ID>             -> permanently deletes a paper (not a soft hide)

PAPER COMMANDS — VALIDATION
-----------------------------
  check-duplicates <json|@file> -> receives a list of candidates (same format as
                                  "results" from search_papers.py) and returns ONLY those
                                  not already present in _papers.json (match on DOI,
                                  arXiv ID or normalized title). Use this ALWAYS before
                                  choosing what to add — avoid discovering duplicates
                                  after having already run queries and read abstracts.

CITATION COMMANDS
------------------
  get-citations <ID>            -> queries Semantic Scholar for the real citations of
                                  paper <ID>. Does NOT write anything to disk.
  apply-citations <ID>          -> like get-citations, but saves the found links.
  sync-citations                -> runs apply-citations on ALL papers in the catalog.

PDF COMMANDS
------------
  fetch-pdf <ID>                -> resolves and downloads an open-access PDF for
                                  paper <ID> (tries arXiv, then Semantic Scholar,
                                  then Unpaywall). Sets file/pdf_source on success,
                                  or pdf_status ("unavailable"/"error: ...") otherwise.
  sync-pdfs                     -> runs fetch-pdf on every paper that doesn't
                                  already have a PDF on disk.

VENUE COMMANDS — READ
----------------------
  venue-list                    -> all venues (ID, name, type)
  venue-get <VID>               -> full record of a venue

VENUE COMMANDS — WRITE
-----------------------
  add-venue <json>              -> adds a venue (ID assigned automatically)
  update-venue <VID> <json>     -> updates fields of an existing venue (partial merge)
  delete-venue <VID> [force]    -> permanently deletes a venue (blocked if papers reference it)

GRAPH COMMANDS
--------------
  graph-status                  -> overview: nodes by type, edges, interactions
  graph-node <id>               -> node with context (edges, recent interactions)
  graph-nodes [--type <type>]   -> list nodes (filterable by type)
  graph-add-node <type> <json>  -> adds a node (concept/project/endpoint/idea/pool)
  graph-update-node <id> <json> -> updates fields of a node (partial merge)
  graph-remove-node <id>        -> removes node and all its edges
  graph-add-edge <src> <tgt> <type> [note] -> adds an edge
  graph-remove-edge <src> <tgt> [--type <type>] -> removes edges
  graph-neighbors <id> [--depth N] [--edge-type <type>] -> BFS traversal
  graph-path <from> <to>        -> shortest path (BFS, max depth 6)
  graph-interact <id> <type> [--weight N] -> logs interaction
  graph-engagement [--top N]    -> engagement ranking (exponential decay)
  graph-search <text>           -> full-text search on nodes and papers
  graph-lint [--stale-days N] [--quiet-days N] -> health-check: orphan nodes,
                                  projects with no papers, dangling refs, stale ideas

EXAMPLES
--------
  python _scripts/papers_api.py list
  python _scripts/papers_api.py get P004
  python _scripts/papers_api.py search "fetal brain"
  python _scripts/papers_api.py graph-status
  python _scripts/papers_api.py graph-node C003
  python _scripts/papers_api.py graph-add-node concept '{"name": "ViT", "area": "CV"}'
  python _scripts/papers_api.py graph-add-edge C003 P004 uses_concept
  python _scripts/papers_api.py graph-neighbors C003 --depth 2
  python _scripts/papers_api.py graph-interact C003 discussed
  python _scripts/papers_api.py graph-engagement --top 5
"""

import os
import re
import sys
import json
import time
import math
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

# -- Paths -----------------------------------------------------------------

# _scripts/ is a subfolder of the project root
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent

# Bundled empty seed files, shipped with the plugin and version-controlled.
TEMPLATES_DIR = ROOT_DIR / "_templates"

# Where the user's actual data lives. Always a fixed directory under the
# user's home, unconditionally — independent of OS, host, or plugin install
# location. Some MCP hosts re-provision the plugin's own source tree fresh
# every session (wiping ROOT_DIR and anything under it) and some don't
# expand placeholder env vars like ${CLAUDE_PLUGIN_DATA} at all, so any data
# dir derived from the plugin's environment can silently reset between
# sessions. A fixed home-directory path is the only location guaranteed to
# survive across sessions, hosts, and plugin updates.
def _resolve_data_dir() -> Path:
    return Path.home() / ".too-many-papers"


DATA_DIR = _resolve_data_dir()

PAPERS_FILE = DATA_DIR / "_papers.json"
VENUES_FILE = DATA_DIR / "_venues.json"
GRAPH_FILE = DATA_DIR / "_graph.json"
# Append-only, machine-written audit trail of every mutation. Unlike
# graph "interactions" (which record conversational/engagement signals and
# are logged explicitly by Claude via graph-interact), every line here is
# written automatically by the command that performs the mutation — Claude
# never has to remember to log anything for this file to stay accurate.
LOG_FILE = DATA_DIR / "_log.jsonl"

# Bibliographic exports (BibTeX now; format-dispatched so RIS/CSL-JSON are
# small additions later). library.bib is regenerated automatically on every
# papers save, the same way _log.jsonl is written automatically.
EXPORT_DIR = DATA_DIR / "exports"
BIBTEX_FILE = EXPORT_DIR / "library.bib"

# Daily paper briefings, one Markdown file per day, all in one folder.
BRIEFINGS_DIR = DATA_DIR / "briefings"


def _ensure_data_files():
    """Create DATA_DIR and seed it from the bundled templates the first time
    it's used. Never overwrites existing data — this only fills in files
    that don't exist yet (fresh install, or a brand new persistent data dir)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for target, template_name in (
        (PAPERS_FILE, "_papers.json"),
        (VENUES_FILE, "_venues.json"),
        (GRAPH_FILE, "_graph.json"),
    ):
        if not target.exists():
            template = TEMPLATES_DIR / template_name
            if template.exists():
                target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                target.write_text("{}", encoding="utf-8")
    if not LOG_FILE.exists():
        LOG_FILE.touch()


_ensure_data_files()

def _configure_stdio():
    """On Windows, redirected stdout (e.g. > file.txt) often uses cp1252 and
    Unicode characters from the CLI (═, ─, emoji) cause UnicodeEncodeError.
    We force UTF-8 with fallback on non-encodable characters."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

# ASCII separators for CLI output (PowerShell redirect on Windows doesn't handle ═/─ well).
SEP_LINE = "-"
SEP_HEAVY = "="

# -- JSON I/O --------------------------------------------------------------

def load_papers():
    with open(PAPERS_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_papers(data):
    # Assign stable cite keys to any new papers, and keep the auto-exported
    # library.bib in sync — both best-effort, and never allowed to break a
    # save (the papers JSON is what matters here).
    try:
        ensure_cite_keys(data.get("papers", {}))
    except Exception:
        pass
    with open(PAPERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        regenerate_bibtex_export(data, load_venues())
    except Exception:
        pass

def load_venues():
    with open(VENUES_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_venues(data):
    with open(VENUES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_graph():
    with open(GRAPH_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_graph(data):
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _log_event(event: str, **fields):
    """Append one JSON-line record to _log.jsonl. Called automatically by
    every mutating command right after its save_*() succeeds — this is
    plumbing, not something Claude has to remember to invoke. Best-effort:
    a logging failure must never break the actual operation that triggered it."""
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        entry.update(fields)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _safe_date(s):
    """Parse an ISO date string, returning None instead of raising on
    anything malformed or missing."""
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None

# -- Helpers ----------------------------------------------------------------

class PayloadError(Exception):
    """Error in parsing/validation of a JSON payload passed by Claude."""

def parse_json_arg(args: list[str]) -> dict | list:
    """Joins the arguments into a string and interprets it as JSON.

    Supports two forms:
      - Inline JSON:  add-paper '{"title": ...}'
      - File:         add-paper @path/file.json

    The @file form avoids having to handle escaping of apostrophes/accents
    in the shell, which in the past broke commands with Italian text
    (e.g. "l'architettura", "gia'").
    """
    if not args:
        raise PayloadError("No payload provided.")
    joined = " ".join(args).strip()
    if joined.startswith("@"):
        file_path = Path(joined[1:].strip())
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.exists():
            raise PayloadError(f"File not found: {file_path}")
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PayloadError(f"Invalid JSON in file {file_path}: {e}")
    try:
        return json.loads(joined)
    except json.JSONDecodeError as e:
        raise PayloadError(
            f"Invalid JSON: {e}. If the text contains apostrophes or accents, "
            f"write the payload to a file and pass @path/file.json instead of inline JSON."
        )

def normalize_title(t) -> str:
    t = _as_str(t).lower()
    t = re.sub(r"[^a-z0-9 ]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

TRUNCATION_PATTERNS = (
    re.compile(r"\bet al\.?\b", re.IGNORECASE),
    re.compile(r"\betc\.?\b", re.IGNORECASE),
    re.compile(r"^\s*\.\.\.\s*$"),
)

def has_truncated_authors(authors) -> str | None:
    """Returns an error message if the author list appears truncated/synthesized
    instead of being the verbatim list, or None if ok."""
    if not isinstance(authors, list) or len(authors) == 0:
        return "The 'authors' field must be a non-empty list of verbatim authors."
    for a in authors:
        if not isinstance(a, str) or not a.strip():
            return "The 'authors' field contains an empty or non-text element."
        for pat in TRUNCATION_PATTERNS:
            if pat.search(a):
                return (f"Author suspected of truncation: '{a}'. Use the COMPLETE "
                        f"and verbatim list of authors from the source, not abbreviations like 'et al.'.")
    return None

def next_paper_id(papers_dict):
    nums = [int(k[1:]) for k in papers_dict if k.startswith("P") and k[1:].isdigit()]
    return f"P{(max(nums) + 1):03d}" if nums else "P001"

def next_venue_id(venues_dict):
    nums = [int(k[1:]) for k in venues_dict if k.startswith("V") and k[1:].isdigit()]
    return f"V{(max(nums) + 1):03d}" if nums else "V001"

def format_paper(pid, p, verbose=False):
    authors = ", ".join(p.get("authors", [])) or "[not available]"
    venue = p.get("venue_detail") or p.get("venue_id") or "—"
    if not verbose:
        return f"{pid} | {p['year']} | {venue:<35} | {p['title'][:70]}"
    lines = [
        f"{'-'*60}",
        f"ID:       {pid}",
        f"Title:    {p['title']}",
        f"Authors:  {authors}",
        f"Year:     {p['year']}",
        f"Discovered: {p.get('discovered', '—')}",
        f"Venue:    {p.get('venue_id','—')} — {p.get('venue_detail','—')}",
        f"Source:   {p.get('source_verified') or '[not available]'}",
        f"URL:      {p.get('url') or '—'}",
        f"Concepts: {', '.join('#' + c for c in p.get('concepts', []))}",
        f"File:     {p.get('file') or '—'}",
        f"Outside:  {'YES' if p.get('outside_zone') else '—'}",
        f"Hidden:   {'YES' if p.get('hidden') else '—'}",
        f"Cites:    {', '.join(p.get('cites', [])) or '—'}",
        f"Cited by: {', '.join(p.get('cited_by', [])) or '—'}",
    ]
    if p.get("notes"):
        lines.append(f"Notes:    {p['notes']}")
    return "\n".join(lines)

def format_venue(vid, v, verbose=False):
    if not verbose:
        return f"{vid} | {v['type']:<12} | {v['name']}"
    m = v.get("metrics", {})
    lines = [
        f"{'-'*60}",
        f"ID:           {vid}",
        f"Name:         {v['name']}",
        f"Type:         {v['type']}",
        f"Publisher:    {v.get('publisher','—')}",
        f"URL:          {v.get('url','—')}",
        f"Open Access:  {'YES' if v.get('open_access') else 'NO'}",
        f"Peer-review:  {'YES' if v.get('peer_reviewed') else 'NO'}",
        f"IF:           {m.get('IF') or '—'}",
        f"H-index:      {m.get('h_index') or '—'}",
        f"Quartile:     {m.get('quartile') or '—'}",
        f"CORE:         {m.get('core') or '—'}",
        f"Acceptance:   {m.get('acceptance_rate') or '—'}",
    ]
    if v.get("notes"):
        lines.append(f"Notes:        {v['notes']}")
    return "\n".join(lines)

# -- Paper commands — read -------------------------------------------------

def cmd_list(args):
    data = load_papers()
    papers = data["papers"]
    print(f"{'ID':<6} {'Year':<6} {'Venue':<35} {'Title'}")
    print(SEP_LINE * 100)
    for pid, p in sorted(papers.items()):
        print(format_paper(pid, p))
    print(f"\nTotal: {len(papers)} papers")

def cmd_get(args):
    if not args:
        print("Usage: get <ID>  e.g. get P004"); return
    pid = args[0].upper()
    data = load_papers()
    p = data["papers"].get(pid)
    if not p:
        print(f"Paper '{pid}' not found."); return
    print(format_paper(pid, p, verbose=True))

def cmd_search(args):
    if not args:
        print("Usage: search <text>"); return
    query = " ".join(args).lower()
    data = load_papers()
    results = []
    for pid, p in data["papers"].items():
        haystack = (p["title"] + " " + " ".join(p.get("authors", []))).lower()
        if query in haystack:
            results.append((pid, p))
    if not results:
        print(f"No papers found for '{query}'."); return
    print(f"{'ID':<6} {'Year':<6} {'Venue':<35} {'Title'}")
    print(SEP_LINE * 100)
    for pid, p in results:
        print(format_paper(pid, p))
    print(f"\n{len(results)} results.")

def cmd_by_concept(args):
    if not args:
        print("Usage: by-concept <CID>  e.g. by-concept C003"); return
    cid = args[0].upper().lstrip("#")
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items() if cid in p.get("concepts", [])]
    if not results:
        print(f"No papers for concept #{cid}."); return
    print(f"Papers linked to #{cid}:")
    print(SEP_LINE * 100)
    for pid, p in sorted(results):
        print(format_paper(pid, p))
    print(f"\n{len(results)} papers.")

def cmd_by_author(args):
    if not args:
        print("Usage: by-author <surname>"); return
    query = " ".join(args).lower()
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items()
               if any(query in a.lower() for a in p.get("authors", []))]
    if not results:
        print(f"No papers with author '{query}'."); return
    for pid, p in sorted(results):
        print(format_paper(pid, p))

def cmd_by_venue(args):
    if not args:
        print("Usage: by-venue <VID>  e.g. by-venue V001"); return
    vid = args[0].upper()
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items() if p.get("venue_id") == vid]
    if not results:
        print(f"No papers for venue '{vid}'."); return
    venues = load_venues()
    vname = venues["venues"].get(vid, {}).get("name", vid)
    print(f"Papers in {vid} — {vname}:")
    print(SEP_LINE * 100)
    for pid, p in sorted(results):
        print(format_paper(pid, p))
    print(f"\n{len(results)} papers.")

def cmd_by_year(args):
    if not args:
        print("Usage: by-year <year>"); return
    try:
        year = int(args[0])
    except ValueError:
        print("Year must be an integer."); return
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items() if p.get("year") == year]
    if not results:
        print(f"No papers from {year}."); return
    for pid, p in sorted(results):
        print(format_paper(pid, p))

def cmd_outside(args):
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items() if p.get("outside_zone")]
    if not results:
        print("No outside zone papers."); return
    print("Papers outside the comfort zone:")
    print(SEP_LINE * 100)
    for pid, p in sorted(results):
        print(format_paper(pid, p))

def cmd_hidden(args):
    data = load_papers()
    results = [(pid, p) for pid, p in data["papers"].items() if p.get("hidden")]
    if not results:
        print("No hidden papers."); return
    print("Hidden papers:")
    print(SEP_LINE * 100)
    for pid, p in sorted(results):
        print(format_paper(pid, p))
    print(f"\n{len(results)} papers.")

def cmd_next_id(args):
    data = load_papers()
    print(next_paper_id(data["papers"]))

# -- Paper commands — write ------------------------------------------------

PAPER_REQUIRED_FIELDS = {"title", "authors", "year", "discovered", "venue_id",
                         "venue_detail", "source_verified", "concepts",
                         "file", "outside_zone", "notes"}
PAPER_OPTIONAL_FIELDS = {"url", "hidden", "cites", "cited_by", "cites_unmatched",
                          "pdf_status", "pdf_source", "pdf_notes", "cite_key"}
PAPER_ALLOWED_FIELDS = PAPER_REQUIRED_FIELDS | PAPER_OPTIONAL_FIELDS

def validate_paper_payload(payload: dict) -> list[str]:
    """Returns a list of errors (empty if the payload is valid).
    All anti-hallucination validation lives here, not in Claude's head."""
    errors = []

    if not isinstance(payload, dict):
        return ["The payload must be a JSON object."]

    unknown = set(payload.keys()) - PAPER_ALLOWED_FIELDS
    if unknown:
        errors.append(f"Unrecognized fields (rejected): {', '.join(sorted(unknown))}. "
                       f"Allowed fields: {', '.join(sorted(PAPER_ALLOWED_FIELDS))}.")

    missing = PAPER_REQUIRED_FIELDS - set(payload.keys())
    if missing:
        errors.append(f"Missing fields: {', '.join(sorted(missing))}")

    sv = payload.get("source_verified")
    if not sv or not isinstance(sv, str) or not sv.strip():
        errors.append("Field 'source_verified' is mandatory and cannot be empty "
                       "(anti-hallucination rule: no slot without a verified source).")
    elif not sv.startswith(("http://", "https://")):
        errors.append(f"'source_verified' does not look like a valid URL: '{sv}'.")

    if "authors" in payload:
        auth_err = has_truncated_authors(payload["authors"])
        if auth_err:
            errors.append(auth_err)

    title = payload.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        errors.append("Field 'title' is mandatory and cannot be empty.")

    year = payload.get("year")
    if year is not None and not isinstance(year, int):
        errors.append(f"Field 'year' must be an integer, received: {year!r}.")

    concepts = payload.get("concepts")
    if concepts is not None and not isinstance(concepts, list):
        errors.append("Field 'concepts' must be a list (even an empty one).")

    venue_id = payload.get("venue_id")
    if venue_id:
        venues = load_venues()
        if venue_id not in venues["venues"]:
            errors.append(f"venue_id '{venue_id}' does not exist in _venues.json. "
                           f"Create it first with add-venue.")

    return errors

def cmd_add_paper(args):
    if not args:
        print("Usage: add-paper '<json>' or add-paper @file.json"); return
    try:
        payload = parse_json_arg(args)
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    errors = validate_paper_payload(payload)
    if errors:
        print("Payload REJECTED — fix and retry:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    data = load_papers()
    new_id = next_paper_id(data["papers"])
    if "url" not in payload:
        payload["url"] = None
    data["papers"][new_id] = payload
    data["_meta"]["total_papers"] = len(data["papers"])
    data["_meta"]["last_updated"] = str(date.today())

    # -- Automatic citation linking (bidirectional) -------------------------
    # Direction 1 — forward: what the new paper cites, according to Semantic Scholar.
    # Requires an S2 call (requires arXiv ID or DOI verbatim in the payload).
    fwd_result = compute_citation_links(new_id, payload, data["papers"])
    if fwd_result["error"]:
        print(f"[INFO] Automatic linking (what {new_id} cites) not performed: "
              f"{fwd_result['error']}")
    else:
        new_fwd_links = _apply_links(data, new_id, fwd_result["matched"], fwd_result["unmatched"])
        if fwd_result["matched"]:
            print(f"[LINK] {new_id} cites {len(fwd_result['matched'])} papers already in catalog "
                  f"({new_fwd_links} new links).")

    # Direction 2 — backlink: who, in the existing catalog, already cited this paper
    # before it existed in _papers.json. No new S2 call: we compare the new paper
    # against the `cites_unmatched` already saved on existing papers from the last
    # sync-citations/apply-citations.
    citers = find_existing_citers_via_unmatched(new_id, payload, data["papers"])
    back_new_links = 0
    for citer_pid in citers:
        back_new_links += _apply_links(data, citer_pid, [new_id])
        # The entry is now resolved (the missing paper was just added):
        # we remove it from cites_unmatched to avoid leaving it duplicated
        # both there and in cites.
        citer = data["papers"][citer_pid]
        new_doi = (extract_doi(payload) or "").lower().strip()
        new_arxiv = (extract_arxiv_id(payload) or "").lower().strip()
        new_title_norm = normalize_title(payload.get("title"))
        remaining = []
        for u in (citer.get("cites_unmatched") or []):
            u_doi = (u.get("doi") or "").lower().strip()
            u_arxiv = (u.get("arxiv_id") or "").lower().strip()
            u_title_norm = normalize_title(u.get("title", ""))
            is_resolved = ((u_doi and new_doi and u_doi == new_doi) or
                           (u_arxiv and new_arxiv and u_arxiv == new_arxiv) or
                           (u_title_norm and new_title_norm and u_title_norm == new_title_norm))
            if not is_resolved:
                remaining.append(u)
        citer["cites_unmatched"] = remaining
    if citers:
        print(f"[LINK] {new_id} was already cited by {len(citers)} papers in the catalog "
              f"({back_new_links} new links): {', '.join(sorted(citers))}")

    # -- Automatic PDF fetch (best-effort) -----------------------------------
    # Wrapped in try/except: a PDF-fetch problem must never block or fail the
    # papers_add call itself — the paper record is what matters here.
    try:
        pdf_result = _fetch_pdf_for_paper(new_id, data["papers"][new_id])
    except Exception as e:
        pdf_result = {"ok": False, "status": f"error: {type(e).__name__}: {e}",
                       "reason": str(e), "source": "none"}
    if pdf_result["ok"]:
        data["papers"][new_id]["file"] = pdf_result["file"]
        data["papers"][new_id]["pdf_source"] = pdf_result["source"]
        print(f"[PDF] {new_id}: fetched from {pdf_result['source']} -> {pdf_result['file']}")
        _log_event("pdf_fetched", id=new_id, source=pdf_result["source"])
    else:
        data["papers"][new_id]["pdf_status"] = pdf_result["status"]
        if pdf_result["status"] == "unavailable":
            print(f"[PDF] {new_id}: no open-access PDF found ({pdf_result['reason']}).")
        else:
            print(f"[PDF] {new_id}: {pdf_result['status']}")
        _log_event("pdf_fetch_failed", id=new_id, status=pdf_result["status"])

    save_papers(data)
    _log_event("paper_added", id=new_id, title=payload.get("title"))

    # -- Keep the graph's uses_concept edges in sync with `concepts` ---------
    concept_ids = data["papers"][new_id].get("concepts") or []
    if concept_ids:
        graph = load_graph()
        added = sync_concept_edges(new_id, concept_ids, graph)
        if added:
            save_graph(graph)
            _log_event("concept_edges_synced", id=new_id, added=added)

    print(f"Paper added with ID: {new_id}")
    print(format_paper(new_id, data["papers"][new_id], verbose=True))

def cmd_check_duplicates(args):
    """Receives a list of candidates (format 'results' from search_papers.py) and
    returns only those NOT already present in _papers.json, marking the discarded ones.
    Claude must call this command BEFORE choosing what to add."""
    if not args:
        print("Usage: check-duplicates '<json>' or check-duplicates @file.json"); return
    try:
        payload = parse_json_arg(args)
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    candidates = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list):
        print("ERROR: the payload must be a list of candidates, or an object "
              "with key 'results' (same format as search_papers.py output).")
        sys.exit(1)

    data = load_papers()
    existing_doi = set()
    existing_arxiv = set()
    existing_title = set()
    for p in data["papers"].values():
        sv = _as_str(p.get("source_verified")).lower()
        if "doi.org/" in sv:
            existing_doi.add(sv.split("doi.org/", 1)[1].strip())
        if "arxiv.org/abs/" in sv:
            existing_arxiv.add(sv.split("arxiv.org/abs/", 1)[1].strip().rstrip("/"))
        existing_title.add(normalize_title(p.get("title", "")))

    kept, dropped = [], []
    for c in candidates:
        doi = (c.get("doi") or "").lower().strip()
        arxiv_id = (c.get("arxiv_id") or "").lower().strip()
        ntitle = normalize_title(c.get("title", ""))

        reason = None
        if doi and doi in existing_doi:
            reason = f"DOI already in catalog ({doi})"
        elif arxiv_id and arxiv_id in existing_arxiv:
            reason = f"arXiv ID already in catalog ({arxiv_id})"
        elif ntitle and ntitle in existing_title:
            reason = "title already in catalog"

        if reason:
            dropped.append({"title": c.get("title", ""), "reason": reason})
        else:
            kept.append(c)

    result = {
        "total_candidates": len(candidates),
        "kept": kept,
        "dropped": dropped,
        "kept_count": len(kept),
        "dropped_count": len(dropped),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

def cmd_update_paper(args):
    if len(args) < 2:
        print("Usage: update-paper <ID> '<json>' or update-paper <ID> @file.json"); return
    pid = args[0].upper()
    try:
        patch = parse_json_arg(args[1:])
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    if not isinstance(patch, dict):
        print("ERROR: the patch must be a JSON object."); sys.exit(1)

    unknown = set(patch.keys()) - PAPER_ALLOWED_FIELDS
    if unknown:
        print(f"ERROR: unrecognized fields (rejected): {', '.join(sorted(unknown))}.")
        sys.exit(1)

    if "authors" in patch:
        auth_err = has_truncated_authors(patch["authors"])
        if auth_err:
            print(f"ERROR: {auth_err}"); sys.exit(1)

    data = load_papers()
    if pid not in data["papers"]:
        print(f"Paper '{pid}' not found."); return
    data["papers"][pid].update(patch)
    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("paper_updated", id=pid, fields=sorted(patch.keys()))

    if "concepts" in patch:
        graph = load_graph()
        added = sync_concept_edges(pid, patch["concepts"] or [], graph)
        if added:
            save_graph(graph)
            _log_event("concept_edges_synced", id=pid, added=added)

    print(f"Paper {pid} updated.")
    print(format_paper(pid, data["papers"][pid], verbose=True))

def cmd_hide(args):
    if not args:
        print("Usage: hide <ID>  e.g. hide P012"); return
    pid = args[0].upper()
    data = load_papers()
    if pid not in data["papers"]:
        print(f"Paper '{pid}' not found."); return
    data["papers"][pid]["hidden"] = True
    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("paper_hidden", id=pid)
    print(f"Paper {pid} hidden.")
    print(format_paper(pid, data["papers"][pid], verbose=True))

def cmd_unhide(args):
    if not args:
        print("Usage: unhide <ID>  e.g. unhide P012"); return
    pid = args[0].upper()
    data = load_papers()
    if pid not in data["papers"]:
        print(f"Paper '{pid}' not found."); return
    data["papers"][pid]["hidden"] = False
    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("paper_unhidden", id=pid)
    print(f"Paper {pid} restored (no longer hidden).")
    print(format_paper(pid, data["papers"][pid], verbose=True))

def cmd_delete_paper(args):
    """Permanently remove a paper from the catalog (not a soft hide).

    Also scrubs the deleted ID out of every other paper's cites/cited_by/
    cites_unmatched arrays so no dangling references are left behind.
    """
    if not args:
        print("Usage: delete-paper <ID>  e.g. delete-paper P012"); return
    pid = args[0].upper()
    data = load_papers()
    if pid not in data["papers"]:
        print(f"Paper '{pid}' not found."); return
    removed = data["papers"].pop(pid)
    scrubbed = 0
    for other in data["papers"].values():
        for field in ("cites", "cited_by", "cites_unmatched"):
            values = other.get(field)
            if values and pid in values:
                other[field] = [v for v in values if v != pid]
                scrubbed += 1
    data["_meta"]["total_papers"] = len(data["papers"])
    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("paper_deleted", id=pid, title=removed.get("title", ""), scrubbed=scrubbed)
    print(f"Paper {pid} ({removed.get('title', '')}) permanently deleted. "
          f"Scrubbed {scrubbed} cross-reference(s) in other papers.")

# -- Citations (Semantic Scholar) -------------------------------------------
#
# Primary source: Semantic Scholar Graph API (api.semanticscholar.org).
# No data here is invented: every reference reported comes from a real HTTP
# response received in this session. If the API doesn't respond or the paper
# doesn't have a resolvable identifier, the function explicitly declares it
# — it infers nothing (Rule Zero / Anti-Hallucination Protocol).

S2_API_BASE = "https://api.semanticscholar.org/graph/v1/paper"
S2_USER_AGENT = "PapersToRead-ResearchAssistant/1.0 (personal academic tool)"

# If the environment variable S2_API_KEY is set, it is sent as the x-api-key
# header on every request to Semantic Scholar (raises rate limits compared to
# anonymous access). Without a key, requests remain anonymous as before.
S2_API_KEY = os.environ.get("S2_API_KEY")

# Minimum interval (seconds) between two consecutive requests to S2, to stay
# within the API key's rate limit (e.g. 1 request/second). Also settable via
# env var S2_MIN_INTERVAL to change it without touching the code.
S2_MIN_INTERVAL = float(os.environ.get(
    "S2_MIN_INTERVAL", "1.0" if S2_API_KEY else "3.5"))
_last_s2_request_time = [0.0]

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
# Matches the 40-hex-char Semantic Scholar paper ID at the end of a
# semanticscholar.org/paper/... URL, with or without a title slug before it
# (e.g. .../paper/c83e6fb0... or .../paper/Some-Title/c83e6fb0...).
S2_ID_RE = re.compile(r"semanticscholar\.org/paper/(?:[^/\s]+/)?([0-9a-f]{40})", re.IGNORECASE)
# Matches a literal PMCID (e.g. "PMC1234567") in a verbatim field.
PMCID_RE = re.compile(r"PMC\d{4,9}", re.IGNORECASE)


def _as_str(value) -> str:
    """Forces a potentially heterogeneous field (bool, None, number, etc.) to a safe
    string usable with .lower()/regex. The _papers.json schema is not uniform
    (e.g. some papers have source_verified as bool instead of string): this helper
    avoids AttributeError/TypeError without inventing or discarding data —
    a non-string value simply becomes an empty string (= 'no match here')."""
    return value if isinstance(value, str) else ""


def extract_arxiv_id(paper: dict) -> str | None:
    """Looks for an arXiv ID in the paper's verbatim fields (venue_detail, source_verified, url).
    Infers nothing: either the pattern is literally present in one of these fields,
    or returns None."""
    if not isinstance(paper, dict):
        return None
    haystacks = [_as_str(paper.get("venue_detail")), _as_str(paper.get("source_verified")),
                 _as_str(paper.get("url"))]
    for h in haystacks:
        m = ARXIV_ID_RE.search(h)
        if m:
            return m.group(1)
    return None


def extract_doi(paper: dict) -> str | None:
    """Looks for a DOI in the paper's verbatim fields. Same logic as extract_arxiv_id:
    no inference, only pattern-match on text already present in the record."""
    if not isinstance(paper, dict):
        return None
    haystacks = [_as_str(paper.get("source_verified")), _as_str(paper.get("venue_detail")),
                 _as_str(paper.get("url"))]
    for h in haystacks:
        if "doi.org/" in h.lower():
            m = DOI_RE.search(h)
            if m:
                return m.group(0).rstrip(".,)")
    return None


def extract_s2_id(paper: dict) -> str | None:
    """Looks for a Semantic Scholar paper ID in the paper's verbatim fields
    — papers discovered via search_semantic_scholar() often have
    source_verified pointing at a semanticscholar.org/paper/<id> page rather
    than a DOI or arXiv URL, and that ID is itself a fully valid, directly
    resolvable Semantic Scholar identifier (S2_API_BASE/{id}), not something
    that needs a DOI/arXiv ID to be looked up. Same no-inference contract as
    extract_arxiv_id/extract_doi: only a literal S2 paper URL yields an ID."""
    if not isinstance(paper, dict):
        return None
    haystacks = [_as_str(paper.get("source_verified")), _as_str(paper.get("venue_detail")),
                 _as_str(paper.get("url"))]
    for h in haystacks:
        m = S2_ID_RE.search(h)
        if m:
            return m.group(1)
    return None


def extract_pmcid(paper: dict) -> str | None:
    """Looks for a literal PMCID (e.g. "PMC1234567") in the paper's verbatim
    fields. Same no-inference contract as extract_arxiv_id/extract_doi: only
    a literal PMCID already present in the record is used."""
    if not isinstance(paper, dict):
        return None
    haystacks = [_as_str(paper.get("venue_detail")), _as_str(paper.get("source_verified")),
                 _as_str(paper.get("url"))]
    for h in haystacks:
        m = PMCID_RE.search(h)
        if m:
            return m.group(0).upper()
    return None


def _s2_throttle():
    """Ensures at least S2_MIN_INTERVAL seconds between two consecutive requests
    to Semantic Scholar, to respect the API key's rate limit (e.g. 1 req/s)."""
    elapsed = time.monotonic() - _last_s2_request_time[0]
    wait = S2_MIN_INTERVAL - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_s2_request_time[0] = time.monotonic()


def s2_request(url: str, max_retries: int = 6, base_delay: float = 2.0):
    """GET to Semantic Scholar with retry/exponential backoff on 429.
    Returns (json_dict, None) on success, or (None, error_str) if after all
    retries the API did not respond. Does not raise silent exceptions:
    the caller must always check the second element before using the first.
    If S2_API_KEY is set, it is sent as the x-api-key header and each
    request is preceded by a throttle of S2_MIN_INTERVAL seconds."""
    headers = {"User-Agent": S2_USER_AGENT}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    req = urllib.request.Request(url, headers=headers)
    last_err = None
    for attempt in range(max_retries):
        try:
            if S2_API_KEY:
                _s2_throttle()
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last_err = "429 Too Many Requests"
                time.sleep(base_delay * (attempt + 1))
                continue
            elif e.code == 404:
                return None, "404 Not Found (paper not present on Semantic Scholar)"
            else:
                last_err = f"HTTP {e.code}: {e.reason}"
                time.sleep(base_delay)
                continue
        except urllib.error.URLError as e:
            last_err = f"Network error: {e.reason}"
            time.sleep(base_delay)
            continue
        except json.JSONDecodeError as e:
            return None, f"Non-JSON response from Semantic Scholar: {e}"
    return None, f"Persistent rate limit after {max_retries} attempts ({last_err})"


def fetch_s2_references(paper: dict) -> tuple[list[dict] | None, str | None]:
    """Retrieves the list of REAL references (from Semantic Scholar) for a local paper.
    Returns (reference_list, None) on success — even an empty list if the paper
    cites nothing according to S2 — or (None, reason) if it was not possible to
    retrieve anything (no resolvable ID, or the API did not respond)."""
    arxiv_id = extract_arxiv_id(paper)
    doi = extract_doi(paper)
    s2_id = extract_s2_id(paper)

    if arxiv_id:
        lookup_url = f"{S2_API_BASE}/arXiv:{arxiv_id}?fields=title,externalIds"
    elif doi:
        lookup_url = f"{S2_API_BASE}/DOI:{doi}?fields=title,externalIds"
    elif s2_id:
        # Papers discovered via search_semantic_scholar() often have
        # source_verified pointing at a semanticscholar.org/paper/<id> page
        # rather than a DOI/arXiv URL — that ID is a directly resolvable S2
        # identifier on its own, no DOI/arXiv needed.
        lookup_url = f"{S2_API_BASE}/{s2_id}?fields=title,externalIds"
    else:
        return None, ("No verbatim arXiv ID, DOI, or Semantic Scholar paper URL "
                       "found in venue_detail/source_verified/url — cannot query "
                       "Semantic Scholar without a real identifier.")

    meta, err = s2_request(lookup_url)
    if err:
        return None, f"Paper lookup on Semantic Scholar failed: {err}"

    s2_paper_id = (meta or {}).get("paperId")
    if not s2_paper_id:
        return None, "Semantic Scholar did not return a valid paperId."

    refs_url = (f"{S2_API_BASE}/{s2_paper_id}/references"
                f"?fields=title,year,externalIds&limit=1000")
    refs_data, err = s2_request(refs_url)
    if err:
        return None, f"Reference retrieval failed: {err}"

    out = []
    for item in (refs_data or {}).get("data") or []:
        cited = item.get("citedPaper") or {}
        if not cited.get("title"):
            continue  # reference without title: S2 has no useful metadata, skip
        ext = cited.get("externalIds") or {}
        out.append({
            "title": cited.get("title"),
            "year": cited.get("year"),
            "doi": ext.get("DOI"),
            "arxiv_id": ext.get("ArXiv"),
        })
    return out, None


def fetch_s2_citing_papers(paper: dict) -> tuple[list[dict] | None, str | None]:
    """Retrieves the list of REAL papers that cite `paper` according to Semantic Scholar
    (endpoint /citations — the inverse of /references). Used for backlinking: when
    a new paper P is added, it discovers if any paper already in the catalog cites it,
    even if that paper was synced before P existed in _papers.json. Same convention as
    fetch_s2_references: (list, None) on success, (None, reason) if retrieval was not
    possible."""
    arxiv_id = extract_arxiv_id(paper)
    doi = extract_doi(paper)
    s2_id = extract_s2_id(paper)

    if arxiv_id:
        lookup_url = f"{S2_API_BASE}/arXiv:{arxiv_id}?fields=title,externalIds"
    elif doi:
        lookup_url = f"{S2_API_BASE}/DOI:{doi}?fields=title,externalIds"
    elif s2_id:
        # Papers discovered via search_semantic_scholar() often have
        # source_verified pointing at a semanticscholar.org/paper/<id> page
        # rather than a DOI/arXiv URL — that ID is a directly resolvable S2
        # identifier on its own, no DOI/arXiv needed.
        lookup_url = f"{S2_API_BASE}/{s2_id}?fields=title,externalIds"
    else:
        return None, ("No verbatim arXiv ID, DOI, or Semantic Scholar paper URL "
                       "found in venue_detail/source_verified/url — cannot query "
                       "Semantic Scholar without a real identifier.")

    meta, err = s2_request(lookup_url)
    if err:
        return None, f"Paper lookup on Semantic Scholar failed: {err}"

    s2_paper_id = (meta or {}).get("paperId")
    if not s2_paper_id:
        return None, "Semantic Scholar did not return a valid paperId."

    cit_url = (f"{S2_API_BASE}/{s2_paper_id}/citations"
               f"?fields=title,year,externalIds&limit=1000")
    cit_data, err = s2_request(cit_url)
    if err:
        return None, f"Citation retrieval failed: {err}"

    out = []
    for item in (cit_data or {}).get("data") or []:
        citing = item.get("citingPaper") or {}
        if not citing.get("title"):
            continue  # citation without title: S2 has no useful metadata, skip
        ext = citing.get("externalIds") or {}
        out.append({
            "title": citing.get("title"),
            "year": citing.get("year"),
            "doi": ext.get("DOI"),
            "arxiv_id": ext.get("ArXiv"),
        })
    return out, None


def compute_backlinks(pid: str, paper: dict, papers_dict: dict):
    """Inverse orchestrator of compute_citation_links: finds which papers ALREADY in
    the catalog cite `paper` (the new paper just added), according to Semantic
    Scholar. Used for automatic backlinking in add-paper — also links the direction
    'old papers that cited this one, before it existed in the catalog'. Same contract
    as compute_citation_links: matched/unmatched/error, no invention — only papers
    actually found by S2 and actually already in the catalog."""
    citing, err = fetch_s2_citing_papers(paper)
    if err:
        return {"paper_id": pid, "matched": [], "unmatched": [], "error": err}

    matched, unmatched = [], []
    for ref in citing or []:
        if not isinstance(ref, dict):
            continue
        local_id = match_reference_to_local(ref, papers_dict)
        if local_id and local_id != pid:
            if local_id not in matched:
                matched.append(local_id)
        else:
            unmatched.append({"title": ref.get("title"), "year": ref.get("year")})
    return {"paper_id": pid, "matched": matched, "unmatched": unmatched, "error": None}


def _ref_matches_paper(ref: dict, p: dict) -> bool:
    """Compares a reference (title/DOI/arXiv ID, S2 format) against a single local
    paper using the three canonical criteria: exact DOI, exact arXiv ID, or
    normalized title. No inference — only literal comparison after normalization.
    Shared function used by match_reference_to_local (ref -> entire catalog) and
    by the backlink in add-paper (new paper -> saved cites_unmatched)."""
    ref_doi = _as_str(ref.get("doi")).lower().strip()
    ref_arxiv = _as_str(ref.get("arxiv_id")).lower().strip()
    ref_title_norm = normalize_title(ref.get("title", ""))

    sv = _as_str(p.get("source_verified")).lower()
    p_doi = None
    if "doi.org/" in sv:
        p_doi = sv.split("doi.org/", 1)[1].strip()
    p_arxiv = extract_arxiv_id(p)
    p_title_norm = normalize_title(p.get("title", ""))

    if ref_doi and p_doi and ref_doi == p_doi:
        return True
    if ref_arxiv and p_arxiv and ref_arxiv.lower() == p_arxiv.lower():
        return True
    if ref_title_norm and p_title_norm and ref_title_norm == p_title_norm:
        return True
    return False


def match_reference_to_local(ref: dict, papers_dict: dict) -> str | None:
    """Maps a reference (title/DOI/arXiv ID from Semantic Scholar) to a paper already
    present in _papers.json. Same anti-duplicate logic as check-duplicates: match on
    DOI, arXiv ID, or normalized title. Returns the local ID (e.g. 'P014') or None
    if the reference is not in the catalog — in that case nothing is created:
    it's just a paper we haven't read yet."""
    for pid, p in papers_dict.items():
        if _ref_matches_paper(ref, p):
            return pid
    return None


def find_existing_citers_via_unmatched(new_pid: str, new_paper: dict, papers_dict: dict) -> list[str]:
    """Automatic backlink WITHOUT calling Semantic Scholar again: scans papers already
    present in the catalog and checks if the new paper appears in their
    `cites_unmatched` (the real references that S2 had already found for that paper,
    but which were not yet in the catalog at the time of the fetch). If the new paper
    matches one of those entries (same DOI/arXiv ID/normalized title), it means that
    existing paper really does cite it — the match was already ascertained by S2
    previously, here we only check if it's now resolvable. Returns the list of local
    IDs that turn out to cite the new paper."""
    citers = []
    new_as_ref = {
        "title": new_paper.get("title"),
        "doi": extract_doi(new_paper),
        "arxiv_id": extract_arxiv_id(new_paper),
    }
    new_doi = (new_as_ref["doi"] or "").lower().strip()
    new_arxiv = (new_as_ref["arxiv_id"] or "").lower().strip()
    new_title_norm = normalize_title(new_as_ref["title"])

    for pid, p in papers_dict.items():
        if pid == new_pid:
            continue
        for unmatched_ref in (p.get("cites_unmatched") or []):
            ref_doi = (unmatched_ref.get("doi") or "").lower().strip()
            ref_arxiv = (unmatched_ref.get("arxiv_id") or "").lower().strip()
            ref_title_norm = normalize_title(unmatched_ref.get("title", ""))
            if ref_doi and new_doi and ref_doi == new_doi:
                citers.append(pid); break
            if ref_arxiv and new_arxiv and ref_arxiv == new_arxiv:
                citers.append(pid); break
            if ref_title_norm and new_title_norm and ref_title_norm == new_title_norm:
                citers.append(pid); break
    return citers


def compute_citation_links(pid: str, paper: dict, papers_dict: dict):
    """Orchestrator: retrieves the real references of `paper` and maps them to the
    local catalog. Returns a dict with: matched (list of cited local IDs), unmatched
    (references outside the catalog, for informational purposes only), error (reason
    if retrieval failed)."""
    refs, err = fetch_s2_references(paper)
    if err:
        return {"paper_id": pid, "matched": [], "unmatched": [], "error": err}

    matched, unmatched = [], []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        local_id = match_reference_to_local(ref, papers_dict)
        if local_id and local_id != pid:
            if local_id not in matched:
                matched.append(local_id)
        else:
            unmatched.append({"title": ref.get("title"), "year": ref.get("year")})
    return {"paper_id": pid, "matched": matched, "unmatched": unmatched, "error": None}


def cmd_get_citations(args):
    if not args:
        print("Usage: get-citations <ID>  e.g. get-citations P002"); return
    pid = args[0].upper()
    data = load_papers()
    paper = data["papers"].get(pid)
    if not paper:
        print(f"Paper '{pid}' not found."); return

    result = compute_citation_links(pid, paper, data["papers"])
    if result["error"]:
        print(f"[WARNING] {pid}: {result['error']}")
        return

    print(f"Real citations found by Semantic Scholar for {pid} — {paper['title'][:60]}")
    print(SEP_LINE * 70)
    if result["matched"]:
        print(f"Cited papers ALREADY present in the catalog ({len(result['matched'])}):")
        for mid in sorted(result["matched"]):
            print(f"  -> {mid} — {data['papers'][mid]['title'][:65]}")
    else:
        print("No cited papers found in the local catalog.")
    if result["unmatched"]:
        print(f"\nOther cited references (not in catalog, {len(result['unmatched'])}):")
        for u in result["unmatched"][:20]:
            print(f"  * {u.get('title','')[:70]} ({u.get('year') or '—'})")
        if len(result["unmatched"]) > 20:
            print(f"  ... and {len(result['unmatched']) - 20} more")
    print("\n(No changes saved — use apply-citations to write to _papers.json)")


def _apply_links(data: dict, pid: str, matched_ids: list[str],
                  unmatched: list[dict] | None = None) -> int:
    """Writes cites/cited_by for a single already-processed paper. Returns the number
    of NEW links written (for the summary). Idempotent.

    Also persists `unmatched` in `cites_unmatched`: the real references found by
    Semantic Scholar that are NOT (yet) in the local catalog. This is used for
    automatic backlinking — when in the future you add one of these papers with
    add-paper, we compare it against the `cites_unmatched` already saved on existing
    papers instead of calling S2 again. `cites_unmatched` represents the state
    according to the last fetch: it is overwritten (not accumulated) on each run,
    so it doesn't grow indefinitely and always stays consistent with `cites`."""
    new_links = 0
    paper = data["papers"][pid]
    cites = list(paper.get("cites") or [])
    for mid in matched_ids:
        if mid not in cites:
            cites.append(mid)
            new_links += 1
        cited_by = list(data["papers"][mid].get("cited_by") or [])
        if pid not in cited_by:
            cited_by.append(pid)
        data["papers"][mid]["cited_by"] = sorted(set(cited_by))
    paper["cites"] = sorted(set(cites))
    if unmatched is not None:
        paper["cites_unmatched"] = unmatched
    return new_links


def cmd_apply_citations(args):
    if not args:
        print("Usage: apply-citations <ID>  e.g. apply-citations P002"); return
    pid = args[0].upper()
    data = load_papers()
    paper = data["papers"].get(pid)
    if not paper:
        print(f"Paper '{pid}' not found."); return

    result = compute_citation_links(pid, paper, data["papers"])
    if result["error"]:
        print(f"[WARNING] {pid}: {result['error']}")
        return

    new_links = _apply_links(data, pid, result["matched"], result["unmatched"])
    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("citations_applied", id=pid, matched=len(result["matched"]), new_links=new_links)

    print(f"{pid}: {len(result['matched'])} links to the catalog "
          f"({new_links} new). cited_by updated on cited papers.")
    if result["matched"]:
        for mid in sorted(result["matched"]):
            print(f"  -> cites {mid} — {data['papers'][mid]['title'][:60]}")


def cmd_sync_citations(args):
    data = load_papers()
    papers = data["papers"]
    total = len(papers)
    processed, total_new_links, no_id, errors = 0, 0, [], []

    for i, (pid, paper) in enumerate(sorted(papers.items()), start=1):
        try:
            result = compute_citation_links(pid, paper, papers)
        except Exception as e:
            errors.append((pid, f"{type(e).__name__}: {e}"))
            print(f"[{i}/{total}] {pid}: unexpected ERROR — {e}")
            continue
        if result["error"]:
            if "No verbatim arXiv ID or DOI" in result["error"]:
                no_id.append(pid)
            else:
                errors.append((pid, result["error"]))
            print(f"[{i}/{total}] {pid}: {result['error']}")
        else:
            new_links = _apply_links(data, pid, result["matched"], result["unmatched"])
            total_new_links += new_links
            processed += 1
            print(f"[{i}/{total}] {pid}: {len(result['matched'])} citations in catalog "
                  f"({new_links} new)")
        time.sleep(1.1)  # respect Semantic Scholar's public rate limit

    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("citations_synced", processed=processed, new_links=total_new_links,
               no_id=len(no_id), errors=len(errors))

    print("\n" + SEP_HEAVY * 70)
    print("SUMMARY sync-citations")
    print(SEP_HEAVY * 70)
    print(f"Total papers:              {total}")
    print(f"Successfully processed:    {processed}")
    print(f"New links written:         {total_new_links}")
    print(f"Without resolvable ID:     {len(no_id)}  {no_id if no_id else ''}")
    print(f"Network/API errors:        {len(errors)}")
    for pid, e in errors:
        print(f"  * {pid}: {e}")

# -- Paper discovery (arXiv, Semantic Scholar, OpenAlex) --------------------
#
# This is the ONLY sanctioned way to find new papers. It exists so Claude
# never has to fall back to WebSearch/WebFetch to locate papers: every
# candidate returned here comes from a real, verifiable API response from
# one of these providers, already deduplicated across providers and against
# the local catalog. No metadata is invented — a field a provider didn't
# return is simply absent, never guessed.

ARXIV_API_BASE = "http://export.arxiv.org/api/query"
OPENALEX_API_BASE = "https://api.openalex.org/works"
DISCOVERY_CONTACT_EMAIL = os.environ.get("TOO_MANY_PAPERS_CONTACT_EMAIL", "")
# OpenAlex requires an API key for reliable search access (their 2026 pricing
# switch left anonymous search with a near-zero daily budget — see
# https://developers.openalex.org/api-reference/authentication). Free to get
# at https://openalex.org/settings/api. Without one we fall back to slow,
# heavily-throttled anonymous requests instead of failing outright.
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
_arxiv_bucket = [0.0]
_openalex_bucket = [0.0]
ARXIV_MIN_INTERVAL = float(os.environ.get("ARXIV_MIN_INTERVAL", "3.0"))
OPENALEX_MIN_INTERVAL = float(os.environ.get(
    "OPENALEX_MIN_INTERVAL", "0.2" if OPENALEX_API_KEY else "2.5"))


def _throttle(bucket: list, min_interval: float):
    elapsed = time.monotonic() - bucket[0]
    wait = min_interval - elapsed
    if wait > 0:
        time.sleep(wait)
    bucket[0] = time.monotonic()


def _http_get(url: str, headers: dict | None = None, timeout: int = 20,
              max_retries: int = 4, base_delay: float = 2.0) -> tuple[str | None, str | None]:
    """Generic GET with retry/backoff on 429/5xx. Returns (body_text, None) on
    success or (None, error_string) if every attempt failed. Never raises."""
    req = urllib.request.Request(url, headers=headers or {})
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8"), None
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                last_err = f"HTTP {e.code}"
                time.sleep(base_delay * (attempt + 1))
                continue
            return None, f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            last_err = f"Network error: {e.reason}"
            time.sleep(base_delay)
            continue
    return None, f"Persistent failure after {max_retries} attempts ({last_err})"


def search_arxiv(query: str, max_results: int = 10, year_from: int | None = None) -> tuple[list[dict], str | None]:
    """Real keyword search against the arXiv API. No auth required."""
    _throttle(_arxiv_bucket, ARXIV_MIN_INTERVAL)
    q = urllib.parse.quote(f"all:{query}")
    url = (f"{ARXIV_API_BASE}?search_query={q}&sortBy=relevance&sortOrder=descending"
           f"&max_results={max_results}")
    body, err = _http_get(url)
    if err:
        return [], f"arXiv request failed: {err}"
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return [], f"arXiv returned unparseable XML: {e}"

    out = []
    for entry in root.findall("atom:entry", _ARXIV_NS):
        title = (entry.findtext("atom:title", default="", namespaces=_ARXIV_NS) or "").strip()
        title = " ".join(title.split())
        if not title:
            continue
        summary = (entry.findtext("atom:summary", default="", namespaces=_ARXIV_NS) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        if year_from and year and year < year_from:
            continue
        authors = [a.findtext("atom:name", default="", namespaces=_ARXIV_NS)
                   for a in entry.findall("atom:author", _ARXIV_NS)]
        entry_id = entry.findtext("atom:id", default="", namespaces=_ARXIV_NS) or ""
        m = ARXIV_ID_RE.search(entry_id)
        arxiv_id = m.group(1) if m else None
        out.append({
            "title": title,
            "authors": [a for a in authors if a],
            "year": year,
            "venue": "arXiv",
            "abstract": summary or None,
            "doi": None,
            "arxiv_id": arxiv_id,
            "url": entry_id or None,
            "source_provider": "arxiv",
        })
    return out, None


def search_semantic_scholar(query: str, max_results: int = 10, year_from: int | None = None) -> tuple[list[dict], str | None]:
    """Real keyword search against the Semantic Scholar Graph API's
    /paper/search endpoint (different from the /paper/{id} lookups used for
    citations, but the same base URL, auth, and rate-limit handling)."""
    fields = "title,authors,year,venue,abstract,externalIds,url"
    q = urllib.parse.quote(query)
    year_param = f"&year={year_from}-" if year_from else ""
    url = f"{S2_API_BASE}/search?query={q}&fields={fields}&limit={max_results}{year_param}"
    data, err = s2_request(url)
    if err:
        if not S2_API_KEY:
            err = (f"{err} (no S2_API_KEY set — anonymous access is heavily "
                   "rate-limited; get a free key at "
                   "https://www.semanticscholar.org/product/api#api-key-form "
                   "and set it as an environment variable for reliable access)")
        return [], f"Semantic Scholar search failed: {err}"
    out = []
    for item in (data or {}).get("data") or []:
        ext = item.get("externalIds") or {}
        out.append({
            "title": item.get("title"),
            "authors": [a.get("name") for a in (item.get("authors") or []) if a.get("name")],
            "year": item.get("year"),
            "venue": item.get("venue") or None,
            "abstract": item.get("abstract"),
            "doi": ext.get("DOI"),
            "arxiv_id": ext.get("ArXiv"),
            "url": item.get("url"),
            "source_provider": "semantic_scholar",
        })
    return out, None


def _openalex_reconstruct_abstract(inv_index: dict | None) -> str | None:
    """OpenAlex returns abstracts as an inverted index (word -> [positions])
    instead of plain text, for copyright reasons. Reconstructing it is a
    pure, lossless rearrangement of words OpenAlex itself returned — not an
    inference."""
    if not inv_index:
        return None
    positions = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort(key=lambda p: p[0])
    return " ".join(w for _, w in positions)


def search_openalex(query: str, max_results: int = 10, year_from: int | None = None) -> tuple[list[dict], str | None]:
    """Real keyword search against the OpenAlex Works API. No auth required;
    a contact email (env var TOO_MANY_PAPERS_CONTACT_EMAIL, if set) is sent
    for OpenAlex's polite pool, which gets faster/more reliable responses."""
    _throttle(_openalex_bucket, OPENALEX_MIN_INTERVAL)
    params = {"search": query, "per_page": str(max(1, min(max_results, 50)))}
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    elif DISCOVERY_CONTACT_EMAIL:
        params["mailto"] = DISCOVERY_CONTACT_EMAIL
    url = f"{OPENALEX_API_BASE}?{urllib.parse.urlencode(params)}"
    body, err = _http_get(url)
    if err:
        if not OPENALEX_API_KEY:
            err = (f"{err} (no OPENALEX_API_KEY set — anonymous access has a "
                   "near-zero daily budget since OpenAlex's 2026 pricing change; "
                   "get a free key at https://openalex.org/settings/api and set "
                   "it as an environment variable for reliable access)")
        return [], f"OpenAlex request failed: {err}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return [], f"OpenAlex returned invalid JSON: {e}"

    out = []
    for item in data.get("results") or []:
        authors = [(a.get("author") or {}).get("display_name")
                   for a in (item.get("authorships") or [])]
        primary_loc = item.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        ids = item.get("ids") or {}
        doi = ids.get("doi")
        if doi:
            doi = doi.replace("https://doi.org/", "")
        out.append({
            "title": item.get("title") or item.get("display_name"),
            "authors": [a for a in authors if a],
            "year": item.get("publication_year"),
            "venue": source.get("display_name"),
            "abstract": _openalex_reconstruct_abstract(item.get("abstract_inverted_index")),
            "doi": doi,
            "arxiv_id": None,
            "url": primary_loc.get("landing_page_url") or item.get("id"),
            "source_provider": "openalex",
        })
    return out, None


DEFAULT_DISCOVERY_PROVIDERS = ["arxiv", "semantic_scholar", "openalex"]
DISCOVERY_PROVIDER_FUNCS = {
    "arxiv": search_arxiv,
    "semantic_scholar": search_semantic_scholar,
    "openalex": search_openalex,
}


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    """Cross-provider dedup: the same paper found by 2+ providers (matched on
    DOI, arXiv ID, or normalized title) is merged into a single entry —
    keeping every non-empty field seen across the duplicates and recording
    which providers found it, instead of showing the same paper 2-3 times."""
    seen_doi, seen_arxiv, seen_title = {}, {}, {}
    merged = []
    for c in candidates:
        doi = (c.get("doi") or "").lower().strip()
        arxiv_id = (c.get("arxiv_id") or "").lower().strip()
        ntitle = normalize_title(c.get("title") or "")

        existing = seen_doi.get(doi) if doi else None
        existing = existing or (seen_arxiv.get(arxiv_id) if arxiv_id else None)
        existing = existing or (seen_title.get(ntitle) if ntitle else None)

        if existing:
            providers = existing.setdefault("source_providers", [existing.get("source_provider")])
            if c.get("source_provider") and c["source_provider"] not in providers:
                providers.append(c["source_provider"])
            for k in ("doi", "arxiv_id", "abstract", "venue", "year"):
                if not existing.get(k) and c.get(k):
                    existing[k] = c[k]
            continue

        c["source_providers"] = [c.get("source_provider")]
        merged.append(c)
        if doi:
            seen_doi[doi] = c
        if arxiv_id:
            seen_arxiv[arxiv_id] = c
        if ntitle:
            seen_title[ntitle] = c
    return merged


def discover_candidates(query="", concept_id=None, seed_paper_ids=None,
                        providers=None, year_from=None, max_results=10):
    """Core paper-discovery logic shared by papers-discover and the briefing.
    Returns the result dict (new_candidates, already_in_catalog, errors, ...).
    Raises ValueError on bad input. Does not print or mutate the catalog."""
    query = (query or "").strip()
    seed_paper_ids = seed_paper_ids or []
    providers = providers or DEFAULT_DISCOVERY_PROVIDERS
    max_results = max(1, min(int(max_results or 10), 50))

    unknown_providers = set(providers) - set(DISCOVERY_PROVIDER_FUNCS)
    if unknown_providers:
        raise ValueError(f"unknown providers: {', '.join(sorted(unknown_providers))}. "
                         f"Available: {', '.join(sorted(DISCOVERY_PROVIDER_FUNCS))}")

    if concept_id:
        graph = load_graph()
        node = graph.get("nodes", {}).get(concept_id)
        if not node:
            raise ValueError(f"concept '{concept_id}' not found in graph.")
        extra = " ".join(x for x in [node.get("name"), node.get("area"), node.get("description")] if x)
        query = f"{query} {extra}".strip() if query else extra

    if not query and not seed_paper_ids:
        raise ValueError("provide 'query' and/or 'seed_paper_ids'.")

    all_candidates, errors = [], []
    if query:
        for p in providers:
            results, err = DISCOVERY_PROVIDER_FUNCS[p](query, max_results=max_results, year_from=year_from)
            if err:
                errors.append(f"{p}: {err}")
            all_candidates.extend(results)

    if seed_paper_ids:
        papers = load_papers()["papers"]
        for pid in seed_paper_ids:
            seed = papers.get(pid)
            if not seed:
                errors.append(f"seed paper '{pid}' not found in catalog")
                continue
            refs, err = fetch_s2_references(seed)
            if err:
                errors.append(f"citations for {pid}: {err}")
                continue
            for r in refs or []:
                r["source_provider"] = "semantic_scholar_citations"
                r.setdefault("authors", [])
                r.setdefault("venue", None)
                r.setdefault("abstract", None)
                r.setdefault("url", None)
                all_candidates.append(r)

    merged = _dedupe_candidates(all_candidates)

    data = load_papers()
    existing_doi, existing_arxiv, existing_title = set(), set(), set()
    for p in data["papers"].values():
        sv = _as_str(p.get("source_verified")).lower()
        if "doi.org/" in sv:
            existing_doi.add(sv.split("doi.org/", 1)[1].strip())
        if "arxiv.org/abs/" in sv:
            existing_arxiv.add(sv.split("arxiv.org/abs/", 1)[1].strip().rstrip("/"))
        existing_title.add(normalize_title(p.get("title", "")))

    new_candidates, already_in_catalog = [], []
    for c in merged:
        doi = (c.get("doi") or "").lower().strip()
        arxiv_id = (c.get("arxiv_id") or "").lower().strip()
        ntitle = normalize_title(c.get("title") or "")
        if ((doi and doi in existing_doi) or (arxiv_id and arxiv_id in existing_arxiv)
                or (ntitle and ntitle in existing_title)):
            already_in_catalog.append(c.get("title"))
        else:
            new_candidates.append(c)

    return {
        "query": query or None,
        "providers_used": providers,
        "total_found_raw": len(all_candidates),
        "total_after_provider_dedup": len(merged),
        "already_in_catalog": already_in_catalog,
        "new_candidates": new_candidates,
        "new_candidates_count": len(new_candidates),
        "errors": errors or None,
    }


def cmd_papers_discover(args):
    """Search external providers for real papers matching a topic, and/or
    expand from the citations of papers already in the catalog. Combines
    cross-provider dedup with the same catalog-dedup logic as
    check-duplicates, so the output is ready to feed straight into add-paper.

    Payload fields:
      query          - topic/keywords (optional if concept_id or seed_paper_ids given)
      concept_id     - graph concept ID; its name/area/description are appended to query
      seed_paper_ids - list of catalog paper IDs; their S2 references are pulled in too
      providers      - subset of arxiv, semantic_scholar, openalex (default: all three)
      year_from      - minimum publication year
      max_results    - per-provider cap before merge/dedup (1-50, default 10)
    """
    if not args:
        print("Usage: papers-discover '<json>'"); return
    try:
        payload = parse_json_arg(args)
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)
    if not isinstance(payload, dict):
        print("ERROR: payload must be a JSON object."); sys.exit(1)

    try:
        result = discover_candidates(
            query=payload.get("query", ""),
            concept_id=payload.get("concept_id"),
            seed_paper_ids=payload.get("seed_paper_ids"),
            providers=payload.get("providers"),
            year_from=payload.get("year_from"),
            max_results=payload.get("max_results") or 10,
        )
    except ValueError as e:
        print(f"ERROR: {e}"); sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))

# -- PDF fetching (arXiv / PMC / bioRxiv / Semantic Scholar / Unpaywall) ----
#
# Automatically resolves and downloads an open-access PDF for a paper already
# in the catalog, using only the named sources below — no scraping of
# publisher/journal sites, no paywall bypass, no WebFetch/WebSearch. Every
# URL is either a known-stable pattern (arXiv, PMC, bioRxiv) or comes from a
# real API response reporting an open-access location; nothing is guessed. Downloads
# are byte-validated (must look like an actual PDF) before the `file` field
# is ever set, so a redirect/HTML error page can never masquerade as a saved
# paper.

UNPAYWALL_API_BASE = "https://api.unpaywall.org/v2"
# Unpaywall needs no API key/signup, just a contact email on every request
# (their "polite pool" convention). Reuses the same contact email as
# OpenAlex's polite pool if a dedicated one isn't set.
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("TOO_MANY_PAPERS_CONTACT_EMAIL", "")

PDF_DIR = DATA_DIR / "pdfs"


def resolve_pdf_candidates(paper: dict) -> tuple[list[tuple[str, str]], str | None]:
    """Builds an ORDERED list of (url, source) candidates to try for `paper`:
      a. arXiv     — the /pdf/{id}.pdf URL pattern is stable and documented,
                      no HTTP call needed to construct it.
      b. PMC       — the pmc.ncbi.nlm.nih.gov/articles/{PMCID}/pdf/ pattern is
                      stable and documented, no HTTP call needed to construct
                      it; used only when a literal PMCID is already on file.
      c. bioRxiv   — the biorxiv.org/content/{doi}v1.full.pdf pattern, used
                      only when "biorxiv" is literally present in the
                      paper's own fields (not inferred from the DOI prefix,
                      which medRxiv also shares) and a DOI is on file. v1 is
                      always a real, downloadable version of the preprint,
                      even if later versions exist.
      d. Semantic Scholar — openAccessPdf field, looked up by DOI, or (if no
                      DOI is on file) directly by Semantic Scholar paper ID
                      when source_verified is a semanticscholar.org/paper/
                      URL — that ID is itself a fully valid, directly
                      resolvable S2 identifier (see extract_s2_id). If S2's
                      response includes an externalIds.DOI we didn't already
                      have, it's a real value from a live API response (not
                      an inference) and gets used for the Unpaywall step too.
      e. Unpaywall — best_oa_location, requires a DOI and a contact email.
    Every source that reports a URL is included (not just the first one) so
    the caller can fall through to the next candidate if an earlier one
    turns out not to be a real PDF when actually downloaded (e.g. a host
    reporting an "open access" link that's really a landing/redirect page).
    Returns (candidates, reason) — `reason` explains why the list is empty
    when it is; an empty list is a normal, expected outcome for paywalled
    papers, not a failure to alarm about."""
    candidates: list[tuple[str, str]] = []
    arxiv_id = extract_arxiv_id(paper)
    doi = extract_doi(paper)
    s2_id = extract_s2_id(paper)
    pmcid = extract_pmcid(paper)
    is_biorxiv = any("biorxiv" in _as_str(paper.get(f)).lower()
                      for f in ("venue_detail", "source_verified", "url"))

    # (a) arXiv
    if arxiv_id:
        candidates.append((f"https://arxiv.org/pdf/{arxiv_id}.pdf", "arxiv"))

    # (b) PMC
    if pmcid:
        candidates.append((f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/", "pmc"))

    # (c) bioRxiv
    if is_biorxiv and doi:
        candidates.append((f"https://www.biorxiv.org/content/{doi}v1.full.pdf", "biorxiv"))

    if not doi and not s2_id:
        if candidates:
            return candidates, None
        return [], ("no verbatim arXiv ID, PMCID, DOI, or Semantic Scholar "
                     "paper URL found in venue_detail/source_verified/url — "
                     "cannot resolve a PDF without a real identifier.")

    # (b) Semantic Scholar — by DOI if we have one, else directly by S2 paper ID.
    if doi:
        lookup_url = f"{S2_API_BASE}/DOI:{doi}?fields=openAccessPdf,externalIds"
    else:
        lookup_url = f"{S2_API_BASE}/{s2_id}?fields=openAccessPdf,externalIds"
    meta, err = s2_request(lookup_url)
    if not err and meta:
        oa = meta.get("openAccessPdf") or {}
        if oa.get("url"):
            candidates.append((oa["url"], "semantic_scholar"))
        if not doi:
            ext = meta.get("externalIds") or {}
            if ext.get("DOI"):
                doi = ext["DOI"]  # real DOI from a live S2 response, not invented

    # (c) Unpaywall
    if not doi:
        if candidates:
            return candidates, None
        return [], "no open-access PDF reported by Semantic Scholar, and no DOI to try Unpaywall"
    if not UNPAYWALL_EMAIL:
        if candidates:
            return candidates, None
        return [], (
            "no UNPAYWALL_EMAIL or TOO_MANY_PAPERS_CONTACT_EMAIL set — Unpaywall "
            "requires a contact email; set one of these environment variables "
            "(any email works, e.g. TOO_MANY_PAPERS_CONTACT_EMAIL=you@x.com) to "
            "enable this source"
        )
    unpaywall_url = f"{UNPAYWALL_API_BASE}/{urllib.parse.quote(doi)}?email={urllib.parse.quote(UNPAYWALL_EMAIL)}"
    body, err = _http_get(unpaywall_url)
    if not err:
        try:
            up_data = json.loads(body)
        except json.JSONDecodeError:
            up_data = {}
        best = up_data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf")
        if not pdf_url:
            # Only fall back to `.url` if it visibly looks like a PDF —
            # otherwise treat as unavailable rather than guess (Rule Zero).
            candidate = best.get("url") or ""
            if candidate.lower().split("?")[0].endswith(".pdf"):
                pdf_url = candidate
        if pdf_url:
            candidates.append((pdf_url, "unpaywall"))

    if candidates:
        return candidates, None
    return [], "no open-access PDF reported by Semantic Scholar or Unpaywall"


def resolve_pdf_url(paper: dict) -> tuple[str | None, str, str | None]:
    """Convenience wrapper around resolve_pdf_candidates() returning just the
    single best (first) candidate. Kept for callers that only care about the
    top choice; _fetch_pdf_for_paper uses resolve_pdf_candidates() directly
    so it can fall through to the next source if the first one's download
    fails validation. Returns (url, source, error) — source is one of
    "arxiv", "pmc", "biorxiv", "semantic_scholar", "unpaywall", or "none"."""
    candidates, err = resolve_pdf_candidates(paper)
    if not candidates:
        return None, "none", err
    url, source = candidates[0]
    return url, source, None


def _http_get_bytes(url: str, timeout: int = 20, max_retries: int = 4,
                     base_delay: float = 2.0) -> tuple[bytes | None, str | None, str | None]:
    """Binary-safe variant of _http_get, for downloading files (PDFs) rather
    than JSON/text — _http_get decodes the body as UTF-8 text, which would
    corrupt binary content. Same retry/backoff behavior on 429/5xx, and
    follows redirects (urllib's default). Returns (body_bytes, content_type,
    None) on success or (None, None, error_string) on failure."""
    headers = {"User-Agent": S2_USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", ""), None
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                last_err = f"HTTP {e.code}"
                time.sleep(base_delay * (attempt + 1))
                continue
            return None, None, f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            last_err = f"Network error: {e.reason}"
            time.sleep(base_delay)
            continue
    return None, None, f"Persistent failure after {max_retries} attempts ({last_err})"


def download_pdf(url: str, dest_path: Path) -> tuple[bool, str | None]:
    """Downloads `url` and writes it to `dest_path` ONLY if it validates as a
    real PDF: Content-Type contains "pdf", OR (when the header is missing or
    wrong, which happens with some hosts) the first 5 bytes of the body are
    the PDF magic number %PDF-. This is the guard against the most common
    failure mode of automatic PDF fetching — a redirect or paywall HTML page
    silently saved as if it were the actual paper. Returns (True, None) on
    success or (False, reason) on failure; never writes an invalid file."""
    body, content_type, err = _http_get_bytes(url)
    if err:
        return False, f"download failed: {err}"
    if not body:
        return False, "download returned an empty body"

    looks_like_pdf = "pdf" in (content_type or "").lower() or body[:5] == b"%PDF-"
    if not looks_like_pdf:
        return False, (f"response does not look like a PDF (Content-Type: "
                        f"'{content_type or 'missing'}', body does not start with %PDF-)")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest_path.write_bytes(body)
    except OSError as e:
        return False, f"failed to write file: {e}"
    return True, None


def _fetch_pdf_for_paper(pid: str, paper: dict) -> dict:
    """Core logic shared by fetch-pdf, sync-pdfs, and the automatic fetch in
    add-paper: resolve PDF candidates and try downloading them IN ORDER,
    falling through to the next source if an earlier one's URL turns out
    not to be a real PDF when actually downloaded (e.g. a host reporting an
    "open access" link that's really a landing/redirect page) — a resolve
    failure is not the same as a download failure, and shouldn't stop the
    fallback chain. Returns a result dict describing the outcome — never
    raises, never mutates `paper` itself (the caller decides what to do with
    the result)."""
    candidates, err = resolve_pdf_candidates(paper)
    if not candidates:
        return {"ok": False, "status": "unavailable",
                "reason": err or "no open-access source found", "source": "none"}

    dest = PDF_DIR / f"{pid}.pdf"
    last_err, last_source = None, None
    for url, source in candidates:
        ok, dl_err = download_pdf(url, dest)
        if ok:
            rel_path = str(dest.relative_to(DATA_DIR)).replace(os.sep, "/")
            return {"ok": True, "file": rel_path, "source": source, "url": url}
        last_err, last_source = dl_err, source

    return {"ok": False, "status": f"error: {last_err}", "reason": last_err,
            "source": last_source}


def cmd_fetch_pdf(args):
    """Resolve and download an open-access PDF for a single paper already in
    the catalog. Tries arXiv, then Semantic Scholar, then Unpaywall (in that
    order — see resolve_pdf_url). On success, sets `file`/`pdf_source` and
    clears any previous `pdf_status`. On failure, sets `pdf_status` instead
    ("unavailable" if no open-access source was found, "error: <reason>" if
    a source was found but the download/validation failed) so the catalog
    records that we tried — never invents a `file` value."""
    if not args:
        print("Usage: fetch-pdf <ID>  e.g. fetch-pdf P014"); return
    pid = args[0].upper()
    data = load_papers()
    paper = data["papers"].get(pid)
    if not paper:
        print(f"Paper '{pid}' not found."); return

    result = _fetch_pdf_for_paper(pid, paper)
    if result["ok"]:
        paper["file"] = result["file"]
        paper["pdf_source"] = result["source"]
        paper.pop("pdf_status", None)
        data["_meta"]["last_updated"] = str(date.today())
        save_papers(data)
        _log_event("pdf_fetched", id=pid, source=result["source"])
        print(f"[PDF] {pid}: fetched from {result['source']} -> {result['file']}")
    else:
        paper["pdf_status"] = result["status"]
        data["_meta"]["last_updated"] = str(date.today())
        save_papers(data)
        _log_event("pdf_fetch_failed", id=pid, status=result["status"])
        if result["status"] == "unavailable":
            print(f"[PDF] {pid}: no open-access PDF found ({result['reason']}).")
        else:
            print(f"[PDF] {pid}: {result['status']}")


def cmd_sync_pdfs(args):
    """Run fetch-pdf logic on every paper in the catalog. Skips papers that
    already have a `file` pointing at a file that actually exists on disk
    (idempotent — safe to re-run, never re-downloads). Mirrors
    cmd_sync_citations's iterate/throttle/SUMMARY pattern."""
    data = load_papers()
    papers = data["papers"]
    total = len(papers)
    fetched, already_had, unavailable, errored = 0, 0, [], []

    for i, (pid, paper) in enumerate(sorted(papers.items()), start=1):
        existing_file = paper.get("file")
        if existing_file and (DATA_DIR / existing_file).exists():
            already_had += 1
            print(f"[{i}/{total}] {pid}: already has a PDF on file, skipped")
            continue

        try:
            result = _fetch_pdf_for_paper(pid, paper)
        except Exception as e:
            errored.append((pid, f"{type(e).__name__}: {e}"))
            print(f"[{i}/{total}] {pid}: unexpected ERROR — {e}")
            continue

        if result["ok"]:
            paper["file"] = result["file"]
            paper["pdf_source"] = result["source"]
            paper.pop("pdf_status", None)
            fetched += 1
            print(f"[{i}/{total}] {pid}: fetched from {result['source']} -> {result['file']}")
        else:
            paper["pdf_status"] = result["status"]
            if result["status"] == "unavailable":
                unavailable.append(pid)
                print(f"[{i}/{total}] {pid}: unavailable ({result['reason']})")
            else:
                errored.append((pid, result["status"]))
                print(f"[{i}/{total}] {pid}: {result['status']}")
        time.sleep(1.1)  # respect the same public rate limits as sync-citations

    data["_meta"]["last_updated"] = str(date.today())
    save_papers(data)
    _log_event("pdfs_synced", fetched=fetched, already_had=already_had,
               unavailable=len(unavailable), errors=len(errored))

    print("\n" + SEP_HEAVY * 70)
    print("SUMMARY sync-pdfs")
    print(SEP_HEAVY * 70)
    print(f"Total papers:              {total}")
    print(f"Fetched this run:          {fetched}")
    print(f"Already had a PDF:         {already_had}")
    print(f"Unavailable (no OA source):{len(unavailable)}  {unavailable if unavailable else ''}")
    print(f"Errors:                    {len(errored)}")
    for pid, e in errored:
        print(f"  * {pid}: {e}")

# -- Venue commands — read -------------------------------------------------

def cmd_venue_list(args):
    data = load_venues()
    print(f"{'ID':<6} {'Type':<14} {'Name'}")
    print(SEP_LINE * 60)
    for vid, v in sorted(data["venues"].items()):
        print(format_venue(vid, v))
    print(f"\nTotal: {len(data['venues'])} venues")

def cmd_venue_get(args):
    if not args:
        print("Usage: venue-get <VID>  e.g. venue-get V003"); return
    vid = args[0].upper()
    data = load_venues()
    v = data["venues"].get(vid)
    if not v:
        print(f"Venue '{vid}' not found."); return
    print(format_venue(vid, v, verbose=True))

# -- Venue commands — write ------------------------------------------------

VENUE_REQUIRED_FIELDS = {"name", "type"}
VENUE_OPTIONAL_FIELDS = {"publisher", "url", "open_access", "peer_reviewed",
                         "metrics", "notes"}
VENUE_ALLOWED_FIELDS = VENUE_REQUIRED_FIELDS | VENUE_OPTIONAL_FIELDS

def validate_venue_payload(payload, is_patch: bool = False) -> list[str]:
    errors = []
    if not isinstance(payload, dict):
        return ["The payload must be a JSON object."]

    unknown = set(payload.keys()) - VENUE_ALLOWED_FIELDS
    if unknown:
        errors.append(f"Unrecognized fields (rejected): {', '.join(sorted(unknown))}. "
                       f"Allowed fields: {', '.join(sorted(VENUE_ALLOWED_FIELDS))}. "
                       f"(Note: the field is called 'peer_reviewed', not 'peer_review'.)")

    if not is_patch:
        missing = VENUE_REQUIRED_FIELDS - set(payload.keys())
        if missing:
            errors.append(f"Missing fields: {', '.join(sorted(missing))}")

    if "name" in payload:
        name = payload["name"]
        if not name or not isinstance(name, str) or not name.strip():
            errors.append("Field 'name' cannot be empty.")
        elif re.search(r"\b(19|20)\d{2}\b", name):
            errors.append(f"The venue 'name' field must not include the year: '{name}'.")

    if "open_access" in payload and not isinstance(payload["open_access"], bool):
        errors.append("Field 'open_access' must be boolean (true/false).")
    if "peer_reviewed" in payload and not isinstance(payload["peer_reviewed"], bool):
        errors.append("Field 'peer_reviewed' must be boolean (true/false).")

    return errors

def cmd_add_venue(args):
    if not args:
        print("Usage: add-venue '<json>' or add-venue @file.json"); return
    try:
        payload = parse_json_arg(args)
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    errors = validate_venue_payload(payload, is_patch=False)
    if errors:
        print("Payload REJECTED — fix and retry:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    data = load_venues()
    new_id = next_venue_id(data["venues"])
    data["venues"][new_id] = payload
    data["_meta"]["total_venues"] = len(data["venues"])
    data["_meta"]["last_updated"] = str(date.today())
    save_venues(data)
    _log_event("venue_added", id=new_id, name=payload.get("name"))
    print(f"Venue added with ID: {new_id}")
    print(format_venue(new_id, payload, verbose=True))

def cmd_update_venue(args):
    if len(args) < 2:
        print("Usage: update-venue <VID> '<json>' or update-venue <VID> @file.json"); return
    vid = args[0].upper()
    try:
        patch = parse_json_arg(args[1:])
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    errors = validate_venue_payload(patch, is_patch=True)
    if errors:
        print("Payload REJECTED — fix and retry:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    data = load_venues()
    if vid not in data["venues"]:
        print(f"Venue '{vid}' not found."); return
    data["venues"][vid].update(patch)
    data["_meta"]["last_updated"] = str(date.today())
    save_venues(data)
    _log_event("venue_updated", id=vid, fields=sorted(patch.keys()))
    print(f"Venue {vid} updated.")
    print(format_venue(vid, data["venues"][vid], verbose=True))

def cmd_delete_venue(args):
    """Permanently remove a venue. Blocked if papers still reference it,
    unless 'force' is passed as an extra argument."""
    if not args:
        print("Usage: delete-venue <VID> [force]  e.g. delete-venue V003"); return
    vid = args[0].upper()
    force = len(args) > 1 and args[1].lower() == "force"
    venues = load_venues()
    if vid not in venues["venues"]:
        print(f"Venue '{vid}' not found."); return
    papers = load_papers()
    referencing = [pid for pid, p in papers["papers"].items() if p.get("venue_id") == vid]
    if referencing and not force:
        print(f"ERROR: venue '{vid}' is still referenced by {len(referencing)} paper(s): "
              f"{', '.join(sorted(referencing))}. Reassign those papers' venue_id first, "
              f"or pass 'force' to delete anyway (leaves those papers pointing at a missing venue).")
        sys.exit(1)
    removed = venues["venues"].pop(vid)
    venues["_meta"]["total_venues"] = len(venues["venues"])
    venues["_meta"]["last_updated"] = str(date.today())
    save_venues(venues)
    _log_event("venue_deleted", id=vid, name=removed.get("name", ""),
               orphaned_papers=len(referencing))
    note = f" ({len(referencing)} paper(s) now reference a missing venue)" if referencing else ""
    print(f"Venue {vid} ({removed.get('name', '')}) permanently deleted.{note}")

# -- Graph helpers ---------------------------------------------------------

GRAPH_NODE_TYPES = {"concept", "project", "endpoint", "idea", "pool", "note"}
GRAPH_EDGE_TYPES = {"connected_to", "uses_concept", "part_of", "inspired_by",
                    "relevant_to", "derived_from", "enables", "annotates"}
INTERACTION_TYPES = {
    "discussed": 3,
    "deepened": 5,
    "paper_requested": 10,
    "read": 2,
    "linked": 8,
}

NODE_REQUIRED_FIELDS = {
    "concept": {"name", "area"},
    "project": {"name", "status"},
    "endpoint": {"name", "status"},
    "idea": {"name", "status", "created"},
    "pool": {"name", "created"},
    "note": {"name", "created"},
}

NODE_OPTIONAL_FIELDS = {
    # `color` is a user-chosen hex color (e.g. "#4a90d9") overriding the
    # default hash-derived color the web UI otherwise assigns per concept
    # name — set from the Concepts tab, never inferred.
    "concept": {"description", "color"},
    "project": {"description"},
    "endpoint": {"description"},
    "idea": {"description", "source"},
    "pool": {"description"},
    # `note` = a reading annotation captured from a PDF (via the web UI's
    # select-to-note flow, or graph_add_note). `quote` is the verbatim
    # excerpt the user selected; `text` is their own comment on it; `page`
    # is where in the PDF it was selected. Linked to its paper via an
    # `annotates` edge (note -> paper), not a field, so it shows up like any
    # other graph relationship (BFS, "linked to" filters, etc.).
    "note": {"quote", "text", "page"},
}


def _resolve_node_id(node_id: str, graph_data: dict) -> dict | None:
    """Resolve a node ID. If it's in the graph nodes, return the node.
    If it starts with P, look in papers. If V, look in venues.
    Returns a dict with at least 'name' or None."""
    nodes = graph_data.get("nodes", {})
    if node_id in nodes:
        return nodes[node_id]
    if node_id.startswith("P") and node_id[1:].isdigit():
        try:
            papers = load_papers()
            p = papers["papers"].get(node_id)
            if p:
                return {"name": p["title"], "type": "paper", "_external": True}
        except Exception:
            pass
    if node_id.startswith("V") and node_id[1:].isdigit():
        try:
            venues = load_venues()
            v = venues["venues"].get(node_id)
            if v:
                return {"name": v["name"], "type": "venue", "_external": True}
        except Exception:
            pass
    return None


def _next_graph_id(nodes: dict, prefix: str, fmt: str = "03d") -> str:
    """Generate next available ID with given prefix."""
    nums = []
    for k in nodes:
        if k.startswith(prefix):
            suffix = k[len(prefix):]
            # Handle numeric suffixes (C001) and slug suffixes (PROJ-FOO)
            if suffix.isdigit():
                nums.append(int(suffix))
    next_num = (max(nums) + 1) if nums else 1
    return f"{prefix}{next_num:{fmt}}"


def _generate_node_id(node_type: str, payload: dict, nodes: dict) -> str:
    """Generate an appropriate ID for the given node type."""
    if node_type == "concept":
        return _next_graph_id(nodes, "C")
    elif node_type == "project":
        slug = re.sub(r"[^A-Z0-9]", "", payload["name"].upper())[:8]
        candidate = f"PROJ-{slug}"
        if candidate in nodes:
            # Add numeric suffix
            i = 2
            while f"{candidate}{i}" in nodes:
                i += 1
            candidate = f"{candidate}{i}"
        return candidate
    elif node_type == "endpoint":
        # Try to find parent project from description or just use generic
        parent_slug = "GEN"
        # Check edges or description for parent project hint
        candidate_base = f"EP-{parent_slug}"
        nums = [int(k.split("-")[-1]) for k in nodes
                if k.startswith(candidate_base + "-") and k.split("-")[-1].isdigit()]
        next_num = (max(nums) + 1) if nums else 1
        return f"{candidate_base}-{next_num}"
    elif node_type == "idea":
        return _next_graph_id(nodes, "IDEA-")
    elif node_type == "pool":
        return _next_graph_id(nodes, "POOL-")
    return _next_graph_id(nodes, node_type.upper()[:4] + "-")


# -- Graph commands --------------------------------------------------------

def cmd_graph_status(args):
    """Overview of graph: node counts by type, edges, interactions.

    Also reports the total paper count from the separate paper catalog
    (_papers.json) alongside the graph's own node/edge counts — papers are
    tracked in their own file and don't need a graph node or edge to exist,
    so without this a catalog full of papers with no graph connections yet
    would otherwise look here like "no papers registered"."""
    graph = load_graph()
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])
    interactions = graph.get("interactions", [])
    total_papers = len(load_papers().get("papers", {}))

    type_counts = {}
    for n in nodes.values():
        t = n.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("GRAPH STATUS")
    print(SEP_HEAVY * 50)
    print(f"Total papers (catalog, independent of graph nodes/edges): {total_papers}")
    print(f"Total nodes: {len(nodes)}")
    for t, c in sorted(type_counts.items()):
        print(f"  {t:<15} {c}")
    print(f"Total edges: {len(edges)}")
    print(f"Total interactions: {len(interactions)}")
    if interactions:
        latest = max(i.get("date", "") for i in interactions)
        print(f"Latest interaction: {latest}")
    print(f"Data directory: {DATA_DIR}")


def cmd_graph_node(args):
    """Get a single node with context (edges, recent interactions)."""
    if not args:
        print("Usage: graph-node <id>  e.g. graph-node C003"); return
    node_id = args[0].upper()
    graph = load_graph()

    node = _resolve_node_id(node_id, graph)
    if not node:
        print(f"Node '{node_id}' not found."); return

    print(SEP_LINE * 60)
    print(f"ID:   {node_id}")
    for k, v in node.items():
        if k.startswith("_"):
            continue
        print(f"{k:<14} {v}")

    # Edges
    edges = graph.get("edges", [])
    related = [e for e in edges if e.get("src") == node_id or e.get("tgt") == node_id]
    if related:
        print(f"\nEdges ({len(related)}):")
        print(SEP_LINE * 60)
        for e in related:
            direction = "->" if e["src"] == node_id else "<-"
            other = e["tgt"] if e["src"] == node_id else e["src"]
            other_node = _resolve_node_id(other, graph)
            other_name = (other_node.get("name", "") if other_node else "?")[:40]
            note = f'  "{e["note"]}"' if e.get("note") else ""
            print(f"  {direction} {other:<12} [{e['type']}] {other_name}{note}")

    # Recent interactions
    interactions = graph.get("interactions", [])
    node_interactions = [i for i in interactions if i.get("node_id") == node_id]
    node_interactions.sort(key=lambda x: x.get("date", ""), reverse=True)
    if node_interactions:
        print(f"\nRecent interactions (last 10):")
        print(SEP_LINE * 60)
        for i in node_interactions[:10]:
            print(f"  {i.get('date','')} | {i.get('type',''):<18} | w={i.get('weight','')}")


def cmd_graph_nodes(args):
    """List all nodes, optionally filtered by type."""
    type_filter = None
    if "--type" in args:
        idx = args.index("--type")
        if idx + 1 < len(args):
            type_filter = args[idx + 1].lower()

    graph = load_graph()
    nodes = graph.get("nodes", {})

    print(f"{'ID':<16} {'Type':<12} {'Name'}")
    print(SEP_LINE * 70)
    count = 0
    for nid, n in sorted(nodes.items()):
        if type_filter and n.get("type", "") != type_filter:
            continue
        print(f"{nid:<16} {n.get('type',''):<12} {n.get('name','')[:45]}")
        count += 1
    print(f"\nTotal: {count} nodes")


def cmd_graph_add_node(args):
    """Add a node to the graph."""
    if len(args) < 2:
        print("Usage: graph-add-node <type> <json>"); return
    node_type = args[0].lower()
    if node_type not in GRAPH_NODE_TYPES:
        print(f"ERROR: type must be one of: {', '.join(sorted(GRAPH_NODE_TYPES))}")
        sys.exit(1)

    try:
        payload = parse_json_arg(args[1:])
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    if not isinstance(payload, dict):
        print("ERROR: the payload must be a JSON object."); sys.exit(1)

    # Validate required fields
    required = NODE_REQUIRED_FIELDS[node_type]
    missing = required - set(payload.keys())
    if missing:
        print(f"ERROR: missing fields for type '{node_type}': {', '.join(sorted(missing))}")
        sys.exit(1)

    # Validate allowed fields
    allowed = required | NODE_OPTIONAL_FIELDS.get(node_type, set()) | {"type"}
    unknown = set(payload.keys()) - allowed
    if unknown:
        print(f"ERROR: unrecognized fields: {', '.join(sorted(unknown))}. "
              f"Allowed: {', '.join(sorted(allowed))}")
        sys.exit(1)

    graph = load_graph()
    nodes = graph.setdefault("nodes", {})
    new_id = _generate_node_id(node_type, payload, nodes)
    payload["type"] = node_type
    nodes[new_id] = payload
    save_graph(graph)
    _log_event("node_added", id=new_id, type=node_type, name=payload.get("name"))

    print(f"Node added with ID: {new_id}")
    print(SEP_LINE * 40)
    for k, v in payload.items():
        print(f"  {k:<14} {v}")


def cmd_graph_update_node(args):
    """Merge-patch a node."""
    if len(args) < 2:
        print("Usage: graph-update-node <id> <json>"); return
    node_id = args[0].upper()

    try:
        patch = parse_json_arg(args[1:])
    except PayloadError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    if not isinstance(patch, dict):
        print("ERROR: the patch must be a JSON object."); sys.exit(1)

    graph = load_graph()
    nodes = graph.get("nodes", {})
    if node_id not in nodes:
        print(f"Node '{node_id}' not found in the graph."); return

    node_type = nodes[node_id].get("type")
    if node_type in NODE_REQUIRED_FIELDS:
        allowed = NODE_REQUIRED_FIELDS[node_type] | NODE_OPTIONAL_FIELDS.get(node_type, set())
        unknown = set(patch.keys()) - allowed
        if unknown:
            print(f"ERROR: unrecognized fields for type '{node_type}': {', '.join(sorted(unknown))}. "
                  f"Allowed: {', '.join(sorted(allowed))}")
            sys.exit(1)
        if "type" in patch and patch["type"] != node_type:
            print("ERROR: a node's type cannot be changed via update.")
            sys.exit(1)

    nodes[node_id].update(patch)
    save_graph(graph)
    _log_event("node_updated", id=node_id, type=node_type, fields=sorted(patch.keys()))
    print(f"Node {node_id} updated.")
    for k, v in nodes[node_id].items():
        print(f"  {k:<14} {v}")


def cmd_graph_remove_node(args):
    """Remove a node and all its edges."""
    if not args:
        print("Usage: graph-remove-node <id>"); return
    node_id = args[0].upper()

    graph = load_graph()
    nodes = graph.get("nodes", {})
    if node_id not in nodes:
        print(f"Node '{node_id}' not found in the graph."); return

    removed_node = nodes.pop(node_id)
    edges_before = len(graph.get("edges", []))
    graph["edges"] = [e for e in graph.get("edges", [])
                      if e.get("src") != node_id and e.get("tgt") != node_id]
    edges_removed = edges_before - len(graph["edges"])

    save_graph(graph)
    _log_event("node_removed", id=node_id, name=removed_node.get("name", ""),
               type=removed_node.get("type"), edges_removed=edges_removed)
    print(f"Removed node {node_id} ({removed_node.get('name','')}) and {edges_removed} edges.")


def sync_concept_edges(pid: str, concept_ids: list, graph: dict) -> int:
    """Ensure a uses_concept edge exists from each concept in concept_ids to pid.

    Additive only: never removes an existing edge, even if a concept is later
    dropped from the paper's `concepts` field. Silently skips concept IDs that
    don't resolve to an actual concept node (the field is free-form and may
    drift). Returns the number of edges added.
    """
    if not concept_ids:
        return 0
    edges = graph.setdefault("edges", [])
    existing = {(e.get("src"), e.get("tgt"), e.get("type")) for e in edges}
    added = 0
    for cid in concept_ids:
        cid = str(cid).upper()
        node = graph.get("nodes", {}).get(cid)
        if not node or node.get("type") != "concept":
            continue
        key = (cid, pid, "uses_concept")
        if key in existing:
            continue
        edges.append({"src": cid, "tgt": pid, "type": "uses_concept"})
        existing.add(key)
        added += 1
    return added


def cmd_graph_add_edge(args):
    """Add an edge between two nodes."""
    if len(args) < 3:
        print("Usage: graph-add-edge <src> <tgt> <type> [note]"); return
    src = args[0].upper()
    tgt = args[1].upper()
    edge_type = args[2].lower()
    note = " ".join(args[3:]).strip() if len(args) > 3 else None
    # Remove --type flag if accidentally passed as note
    if note and note.startswith("--"):
        note = None

    if edge_type not in GRAPH_EDGE_TYPES:
        print(f"ERROR: edge type must be one of: {', '.join(sorted(GRAPH_EDGE_TYPES))}")
        sys.exit(1)

    graph = load_graph()

    # Validate src and tgt exist
    src_node = _resolve_node_id(src, graph)
    if not src_node:
        print(f"ERROR: source node '{src}' not found."); sys.exit(1)
    tgt_node = _resolve_node_id(tgt, graph)
    if not tgt_node:
        print(f"ERROR: target node '{tgt}' not found."); sys.exit(1)

    # Check duplicate
    edges = graph.setdefault("edges", [])
    for e in edges:
        if e.get("src") == src and e.get("tgt") == tgt and e.get("type") == edge_type:
            print(f"Edge already exists: {src} -> {tgt} [{edge_type}]"); return

    new_edge = {"src": src, "tgt": tgt, "type": edge_type}
    if note:
        new_edge["note"] = note
    edges.append(new_edge)

    # Structural fact, not a conversational judgment call: creating a
    # relevant_to/uses_concept edge IS the "linked" engagement signal by
    # definition, so it's recorded automatically here instead of requiring
    # Claude to remember a separate graph-interact call for it.
    auto_linked = []
    if edge_type in ("relevant_to", "uses_concept"):
        interactions = graph.setdefault("interactions", [])
        linked_weight = INTERACTION_TYPES.get("linked", 8)
        today_str = str(date.today())
        for nid in (src, tgt):
            interactions.append({
                "node": nid, "type": "linked", "weight": linked_weight, "date": today_str,
            })
            auto_linked.append(nid)

    save_graph(graph)
    _log_event("edge_added", src=src, tgt=tgt, type=edge_type, auto_linked=auto_linked)

    src_name = src_node.get("name", "")[:30]
    tgt_name = tgt_node.get("name", "")[:30]
    print(f"Edge added: {src} ({src_name}) -> {tgt} ({tgt_name}) [{edge_type}]")
    if auto_linked:
        print(f"[ENGAGEMENT] 'linked' interaction auto-logged for: {', '.join(auto_linked)}")


def cmd_graph_remove_edge(args):
    """Remove edges between src and tgt, optionally filtered by type."""
    if len(args) < 2:
        print("Usage: graph-remove-edge <src> <tgt> [--type <type>]"); return
    src = args[0].upper()
    tgt = args[1].upper()
    type_filter = None
    if "--type" in args:
        idx = args.index("--type")
        if idx + 1 < len(args):
            type_filter = args[idx + 1].lower()

    graph = load_graph()
    edges = graph.get("edges", [])
    before = len(edges)
    graph["edges"] = [e for e in edges
                      if not (e.get("src") == src and e.get("tgt") == tgt and
                              (type_filter is None or e.get("type") == type_filter))]
    removed = before - len(graph["edges"])
    save_graph(graph)
    _log_event("edge_removed", src=src, tgt=tgt, type=type_filter, count=removed)
    print(f"Removed {removed} edges between {src} and {tgt}.")


def cmd_graph_neighbors(args):
    """BFS traversal from a node."""
    if not args:
        print("Usage: graph-neighbors <id> [--depth N] [--edge-type <type>]"); return
    node_id = args[0].upper()
    depth = 1
    edge_type_filter = None

    if "--depth" in args:
        idx = args.index("--depth")
        if idx + 1 < len(args):
            depth = min(int(args[idx + 1]), 3)
    if "--edge-type" in args:
        idx = args.index("--edge-type")
        if idx + 1 < len(args):
            edge_type_filter = args[idx + 1].lower()

    graph = load_graph()
    edges = graph.get("edges", [])

    # Build adjacency
    def get_neighbors(nid):
        result = []
        for e in edges:
            if edge_type_filter and e.get("type") != edge_type_filter:
                continue
            if e.get("src") == nid:
                result.append((e["tgt"], e["type"], "->"))
            elif e.get("tgt") == nid:
                result.append((e["src"], e["type"], "<-"))
        return result

    # BFS
    visited = {node_id}
    queue = deque([(node_id, 0)])
    tree = []  # (node_id, depth, edge_type, direction)

    while queue:
        current, d = queue.popleft()
        if d >= depth:
            continue
        for neighbor, etype, direction in get_neighbors(current):
            if neighbor not in visited:
                visited.add(neighbor)
                tree.append((neighbor, d + 1, etype, direction))
                queue.append((neighbor, d + 1))

    root_node = _resolve_node_id(node_id, graph)
    root_name = root_node.get("name", "") if root_node else "?"
    print(f"Neighbors of {node_id} ({root_name}) — depth {depth}")
    print(SEP_LINE * 60)
    if not tree:
        print("  (no neighbors found)")
    else:
        for nid, d, etype, direction in tree:
            indent = "  " * d
            n = _resolve_node_id(nid, graph)
            name = (n.get("name", "") if n else "?")[:40]
            print(f"{indent}{direction} {nid:<14} [{etype}] {name}")
    print(f"\n{len(tree)} reachable nodes.")


def cmd_graph_path(args):
    """Shortest path between two nodes via BFS."""
    if len(args) < 2:
        print("Usage: graph-path <from> <to>"); return
    start = args[0].upper()
    end = args[1].upper()
    max_depth = 6

    graph = load_graph()
    edges = graph.get("edges", [])

    # Build adjacency
    adj = {}
    for e in edges:
        s, t, et = e["src"], e["tgt"], e["type"]
        adj.setdefault(s, []).append((t, et))
        adj.setdefault(t, []).append((s, et))

    # BFS
    visited = {start}
    queue = deque([(start, [(start, None)])])

    while queue:
        current, path = queue.popleft()
        if current == end:
            print(f"Path found ({len(path) - 1} steps):")
            print(SEP_LINE * 60)
            for i, (nid, etype) in enumerate(path):
                n = _resolve_node_id(nid, graph)
                name = (n.get("name", "") if n else "?")[:45]
                if etype:
                    print(f"  [{etype}]")
                print(f"  {nid:<14} {name}")
            return
        if len(path) > max_depth:
            continue
        for neighbor, etype in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [(neighbor, etype)]))

    print(f"No path found between {start} and {end} (max depth {max_depth}).")


def cmd_graph_interact(args):
    """Log an interaction with a node."""
    if not args:
        print("Usage: graph-interact <id> <type> [--weight N]"); return
    if len(args) < 2:
        print("Usage: graph-interact <id> <type> [--weight N]"); return
    node_id = args[0].upper()
    int_type = args[1].lower()

    if int_type not in INTERACTION_TYPES:
        print(f"ERROR: interaction type must be one of: {', '.join(sorted(INTERACTION_TYPES))}")
        sys.exit(1)

    weight = INTERACTION_TYPES[int_type]
    if "--weight" in args:
        idx = args.index("--weight")
        if idx + 1 < len(args):
            try:
                weight = int(args[idx + 1])
            except ValueError:
                print("ERROR: --weight must be an integer."); sys.exit(1)

    graph = load_graph()

    # Validate node exists
    node = _resolve_node_id(node_id, graph)
    if not node:
        print(f"ERROR: node '{node_id}' not found."); sys.exit(1)

    interactions = graph.setdefault("interactions", [])
    interactions.append({
        "node": node_id,
        "type": int_type,
        "weight": weight,
        "date": str(date.today()),
    })
    save_graph(graph)
    _log_event("interaction_logged", node=node_id, type=int_type, weight=weight)

    name = node.get("name", "")[:40]
    print(f"Interaction logged: {node_id} ({name}) | {int_type} | w={weight} | {date.today()}")


def rank_engagement(graph, top_n=10):
    """Rank graph nodes by engagement score (exponential decay over the logged
    interactions). Returns a list of (node_id, info) sorted by score desc.
    Reusable by cmd_graph_engagement and the briefing."""
    interactions = graph.get("interactions", [])
    today = date.today()
    scores = {}  # node_id -> {score, last_date, recent, older}
    for i in interactions:
        nid = i.get("node", i.get("node_id", ""))
        w = i.get("weight", 1)
        d = i.get("date", "")
        try:
            i_date = date.fromisoformat(d)
        except (ValueError, TypeError):
            continue
        weeks = (today - i_date).days / 7.0
        decay = 0.7 ** weeks
        if nid not in scores:
            scores[nid] = {"score": 0.0, "last_date": d, "recent": 0, "older": 0}
        scores[nid]["score"] += w * decay
        if d > scores[nid]["last_date"]:
            scores[nid]["last_date"] = d
        if weeks <= 2:
            scores[nid]["recent"] += 1
        else:
            scores[nid]["older"] += 1
    return sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)[:top_n]


def cmd_graph_engagement(args):
    """Compute engagement scores with exponential decay."""
    top_n = 10
    if "--top" in args:
        idx = args.index("--top")
        if idx + 1 < len(args):
            try:
                top_n = int(args[idx + 1])
            except ValueError:
                pass

    graph = load_graph()
    if not graph.get("interactions"):
        print("No interactions logged."); return

    ranked = rank_engagement(graph, top_n=top_n)

    print(f"{'ID':<16} {'Name':<30} {'Score':>7} {'Last':>12} {'Trend'}")
    print(SEP_LINE * 80)
    for nid, info in ranked:
        node = _resolve_node_id(nid, graph)
        name = (node.get("name", "") if node else "?")[:28]
        # Trend: compare recent vs older interactions
        if info["recent"] > info["older"]:
            trend = "UP"
        elif info["recent"] < info["older"]:
            trend = "DOWN"
        else:
            trend = "STABLE"
        print(f"{nid:<16} {name:<30} {info['score']:>7.1f} {info['last_date']:>12} {trend}")


def cmd_graph_lint(args):
    """Health-check the graph and paper catalog for common hygiene issues.
    Read-only by default — reports problems, never fixes them automatically
    — except with --fix, which additionally removes nodes/edges whose type
    isn't in the centralized GRAPH_NODE_TYPES/GRAPH_EDGE_TYPES sets (a type
    that could only have gotten there by bypassing the normal add-node/
    add-edge validation, e.g. a direct file edit). Run this occasionally to
    catch orphaned nodes, dead references, and stale ideas before they pile
    up."""
    stale_idea_days = 90
    quiet_days = 45
    do_fix = "--fix" in args
    if "--stale-days" in args:
        idx = args.index("--stale-days")
        if idx + 1 < len(args):
            try:
                stale_idea_days = int(args[idx + 1])
            except ValueError:
                pass
    if "--quiet-days" in args:
        idx = args.index("--quiet-days")
        if idx + 1 < len(args):
            try:
                quiet_days = int(args[idx + 1])
            except ValueError:
                pass

    graph = load_graph()
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])
    interactions = graph.get("interactions", [])

    try:
        papers = load_papers().get("papers", {})
    except Exception:
        papers = {}
    try:
        venues = load_venues().get("venues", {})
    except Exception:
        venues = {}

    today = date.today()
    issues = {
        "orphan_nodes": [], "projects_without_papers": [], "orphan_papers": [],
        "stale_ideas": [], "broken_venue_refs": [], "dangling_citations": [],
        "quiet_concepts": [], "invalid_type_nodes": [], "invalid_type_edges": [],
        "concept_edge_mismatches": [],
    }

    # Nodes/edges whose type isn't in the centralized GRAPH_NODE_TYPES/
    # GRAPH_EDGE_TYPES sets — the single source of truth for what's
    # "official" (also enforced by graph-add-node/graph-add-edge). Anything
    # outside these sets could only have gotten into _graph.json by
    # bypassing that validation (e.g. a direct file edit).
    for nid, n in nodes.items():
        t = n.get("type")
        if t not in GRAPH_NODE_TYPES:
            issues["invalid_type_nodes"].append({"id": nid, "type": t, "name": n.get("name", "")})
    invalid_node_ids = {i["id"] for i in issues["invalid_type_nodes"]}
    for e in edges:
        t = e.get("type")
        if t not in GRAPH_EDGE_TYPES:
            issues["invalid_type_edges"].append(
                {"src": e.get("src"), "tgt": e.get("tgt"), "type": t})

    touched = set()
    incoming_by_tgt = {}
    for e in edges:
        touched.add(e.get("src"))
        touched.add(e.get("tgt"))
        incoming_by_tgt.setdefault(e.get("tgt"), []).append(e)

    # Orphan graph nodes: no edges at all, in either direction.
    for nid, n in nodes.items():
        if nid not in touched:
            issues["orphan_nodes"].append(
                {"id": nid, "type": n.get("type"), "name": n.get("name", "")})

    # Projects with no paper marked relevant_to them.
    for nid, n in nodes.items():
        if n.get("type") != "project":
            continue
        has_paper = any(
            e.get("type") == "relevant_to" and str(e.get("src", "")).startswith("P")
            for e in incoming_by_tgt.get(nid, [])
        )
        if not has_paper:
            issues["projects_without_papers"].append({"id": nid, "name": n.get("name", "")})

    # Papers with no concept tag and no graph edge at all — read but never filed.
    for pid, p in papers.items():
        if p.get("hidden"):
            continue
        if pid not in touched and not p.get("concepts"):
            issues["orphan_papers"].append({"id": pid, "title": p.get("title", "")})

    # `concepts` field vs. `uses_concept` edges — the two representations of
    # the same paper-concept relationship should agree; report drift so it
    # can be backfilled with --fix instead of silently accumulating.
    for pid, p in papers.items():
        listed = {str(c).upper() for c in (p.get("concepts") or [])}
        edged = {
            e.get("src") for e in incoming_by_tgt.get(pid, [])
            if e.get("type") == "uses_concept"
        }
        missing_edges = sorted(listed - edged)
        extra_edges = sorted(edged - listed)
        if missing_edges or extra_edges:
            issues["concept_edge_mismatches"].append({
                "id": pid, "missing_edges": missing_edges, "extra_edges": extra_edges,
            })

    # Ideas not marked done/discarded, old, and with no recent interaction.
    CLOSED_STATUSES = {"done", "completed", "discarded", "closed", "abandoned"}
    for nid, n in nodes.items():
        if n.get("type") != "idea":
            continue
        if (n.get("status") or "").lower() in CLOSED_STATUSES:
            continue
        created = _safe_date(n.get("created"))
        if created is None:
            continue
        age_days = (today - created).days
        if age_days < stale_idea_days:
            continue
        recent = any(
            i.get("node") == nid and _safe_date(i.get("date")) is not None and
            (today - _safe_date(i.get("date"))).days <= stale_idea_days
            for i in interactions
        )
        if not recent:
            issues["stale_ideas"].append({
                "id": nid, "name": n.get("name", ""), "age_days": age_days,
                "status": n.get("status"),
            })

    # Papers pointing at a venue_id that no longer exists.
    for pid, p in papers.items():
        vid = p.get("venue_id")
        if vid and vid not in venues:
            issues["broken_venue_refs"].append({"paper": pid, "venue_id": vid})

    # cites/cited_by pointing at a paper ID no longer in the catalog.
    for pid, p in papers.items():
        for field in ("cites", "cited_by"):
            for ref in (p.get(field) or []):
                if ref not in papers:
                    issues["dangling_citations"].append(
                        {"paper": pid, "field": field, "missing_id": ref})

    # Concepts that have edges but haven't seen an interaction in a while.
    last_interaction_by_node = {}
    for i in interactions:
        nid = i.get("node")
        d = _safe_date(i.get("date"))
        if nid and d and (nid not in last_interaction_by_node or d > last_interaction_by_node[nid]):
            last_interaction_by_node[nid] = d
    for nid, n in nodes.items():
        if n.get("type") != "concept" or nid not in touched:
            continue
        last = last_interaction_by_node.get(nid)
        if last is None or (today - last).days > quiet_days:
            issues["quiet_concepts"].append({
                "id": nid, "name": n.get("name", ""),
                "last_interaction": str(last) if last else "never",
            })

    total = sum(len(v) for v in issues.values())
    if total == 0:
        print("Graph lint: no issues found. Everything looks healthy.")
        return

    print(f"Graph lint: {total} issue(s) found.")
    print(SEP_HEAVY * 70)
    labels = {
        "orphan_nodes": "Orphan nodes (no edges at all)",
        "projects_without_papers": "Projects with no papers linked",
        "orphan_papers": "Papers not linked to any concept or graph node",
        "stale_ideas": f"Ideas untouched for {stale_idea_days}+ days, not closed",
        "broken_venue_refs": "Papers pointing to a missing venue",
        "dangling_citations": "cites/cited_by pointing to a missing paper",
        "quiet_concepts": f"Concepts with no interaction in {quiet_days}+ days",
        "invalid_type_nodes": "Nodes with a type outside GRAPH_NODE_TYPES (not official)",
        "invalid_type_edges": "Edges with a type outside GRAPH_EDGE_TYPES (not official)",
        "concept_edge_mismatches": "Papers where `concepts` and uses_concept edges disagree",
    }
    for key, label in labels.items():
        items = issues[key]
        if not items:
            continue
        print(f"\n[{label}] ({len(items)})")
        for it in items:
            print(f"  {json.dumps(it, ensure_ascii=False)}")

    if not do_fix:
        if issues["invalid_type_nodes"] or issues["invalid_type_edges"]:
            print("\nRun graph-lint --fix to remove the non-official nodes/edges above.")
        if issues["concept_edge_mismatches"]:
            print("\nRun graph-lint --fix to backfill missing uses_concept edges above.")
        return

    if (not issues["invalid_type_nodes"] and not issues["invalid_type_edges"]
            and not issues["concept_edge_mismatches"]):
        print("\n--fix: nothing to fix.")
        return

    edges_before = len(edges)
    fixed_edges = [
        e for e in edges
        if e.get("type") in GRAPH_EDGE_TYPES
        and e.get("src") not in invalid_node_ids
        and e.get("tgt") not in invalid_node_ids
    ]
    for nid in invalid_node_ids:
        nodes.pop(nid, None)
    edges_removed = edges_before - len(fixed_edges)
    graph["nodes"] = nodes
    graph["edges"] = fixed_edges

    # Backfill missing uses_concept edges. Additive only — a listed concept
    # with no edge gets one added; an edge with no matching list entry is left
    # alone (the field, not the edge, is treated as possibly stale/free-form).
    edges_added = 0
    for mismatch in issues["concept_edge_mismatches"]:
        edges_added += sync_concept_edges(mismatch["id"], mismatch["missing_edges"], graph)

    save_graph(graph)
    _log_event("graph_lint_fix", nodes_removed=len(invalid_node_ids),
               edges_removed=edges_removed, concept_edges_added=edges_added)
    print(f"\n--fix: removed {len(invalid_node_ids)} non-official node(s), "
          f"{edges_removed} non-official/dangling edge(s), "
          f"and backfilled {edges_added} missing uses_concept edge(s).")


def cmd_graph_search(args):
    """Full-text search across nodes and papers."""
    if not args:
        print("Usage: graph-search <text>"); return
    query = " ".join(args).lower()

    graph = load_graph()
    nodes = graph.get("nodes", {})

    # Search graph nodes
    results_by_type = {}
    for nid, n in nodes.items():
        haystack = (n.get("name", "") + " " + n.get("description", "")).lower()
        if query in haystack:
            t = n.get("type", "unknown")
            results_by_type.setdefault(t, []).append((nid, n))

    # Search papers
    paper_results = []
    try:
        papers = load_papers()
        for pid, p in papers["papers"].items():
            if query in p.get("title", "").lower():
                paper_results.append((pid, p))
    except Exception:
        pass

    total = sum(len(v) for v in results_by_type.values()) + len(paper_results)
    if total == 0:
        print(f"No results for '{query}'."); return

    print(f"Results for '{query}' ({total} found):")
    print(SEP_LINE * 70)

    for t, items in sorted(results_by_type.items()):
        print(f"\n[{t.upper()}]")
        for nid, n in items:
            print(f"  {nid:<16} {n.get('name','')[:55]}")

    if paper_results:
        print(f"\n[PAPER]")
        for pid, p in paper_results[:20]:
            print(f"  {pid:<8} ({p.get('year','')}) {p['title'][:55]}")
        if len(paper_results) > 20:
            print(f"  ... and {len(paper_results) - 20} more")


# -- Dispatch --------------------------------------------------------------

# =============================================================================
# Bibliographic export (BibTeX) + output validation
# =============================================================================
#
# One canonical generator + validator lives here, in the module that already
# owns DOI/arXiv extraction and the source_verified discipline. The MCP tool
# and the web UI both go through it (the web UI shells out to `export`), so
# there is never a second, drifting implementation.

_LATEX_SPECIALS = {
    '\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
    '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
}
# Title-key stopwords skipped when picking the "significant" first word.
_CITEKEY_STOP = {"the", "a", "an", "of", "on", "in", "for", "and", "to",
                 "with", "from", "using", "via", "by", "at", "into"}


def _latex_escape(s) -> str:
    """Escape the characters that otherwise break a .bib file. Processed on the
    original string so replacements (which contain braces themselves) are never
    re-escaped."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in s)


def _year_digits(paper: dict) -> str:
    return re.sub(r"[^0-9]", "", str(paper.get("year") or ""))[:4]


def cite_key_base(paper: dict) -> str:
    """Deterministic cite key from first-author surname + year + first
    significant title word, e.g. 'ge2026multimodal'. Deterministic so it stays
    stable across exports even before it's persisted."""
    authors = paper.get("authors") or []
    surname = "anon"
    if authors and isinstance(authors[0], str) and authors[0].strip():
        surname = re.sub(r"[^A-Za-z]", "", authors[0].split()[0]) or "anon"
    word = ""
    for w in re.findall(r"[A-Za-z]+", paper.get("title") or ""):
        if w.lower() not in _CITEKEY_STOP:
            word = w.lower()
            break
    return (surname.lower() + _year_digits(paper) + word) or "ref"


def ensure_cite_keys(papers: dict) -> bool:
    """Assign a stable, unique cite_key to any paper missing one. Existing keys
    are never changed (so \\cite{...} in the user's LaTeX keeps working), and
    new collisions get a/b/c suffixes. Returns True if anything changed."""
    changed = False
    used = {p["cite_key"] for p in papers.values()
            if isinstance(p, dict) and p.get("cite_key")}
    for p in papers.values():
        if not isinstance(p, dict) or p.get("cite_key"):
            continue
        base = cite_key_base(p)
        key, i = base, 0
        while key in used:
            i += 1
            key = base + chr(ord('a') + i - 1)
        p["cite_key"] = key
        used.add(key)
        changed = True
    return changed


def _bibtex_entry(pid: str, paper: dict, venues: dict) -> tuple[str, str, dict]:
    """Return (cite_key, entry_text, meta) for one paper. meta feeds validation."""
    key = paper.get("cite_key") or cite_key_base(paper)
    venue = venues.get(paper.get("venue_id")) or {}
    vtype = (venue.get("type") or "").lower()
    arxiv = extract_arxiv_id(paper)
    doi = extract_doi(paper)
    url = _as_str(paper.get("url")) or None
    year = _year_digits(paper)
    vname = venue.get("name") or _as_str(paper.get("venue_detail")) or ""
    authors = " and ".join(a for a in (paper.get("authors") or [])
                           if isinstance(a, str) and a.strip())

    fields: list[tuple[str, str]] = []
    if authors:
        fields.append(("author", _latex_escape(authors)))
    # Double-brace the title to preserve its capitalization in BibTeX styles.
    fields.append(("title", "{" + _latex_escape(paper.get("title") or "") + "}"))
    if year:
        fields.append(("year", year))

    if vtype == "journal":
        etype = "article"
        if vname:
            fields.append(("journal", _latex_escape(vname)))
    elif vtype in ("conference", "workshop"):
        etype = "inproceedings"
        if vname:
            fields.append(("booktitle", _latex_escape(vname)))
    elif arxiv or vtype == "preprint":
        etype = "misc"
        if arxiv:
            fields.append(("eprint", arxiv))
            fields.append(("archivePrefix", "arXiv"))
        fields.append(("howpublished", _latex_escape(vname) or "Preprint"))
    else:
        etype = "misc"
        if vname:
            fields.append(("howpublished", _latex_escape(vname)))

    if venue.get("publisher") and etype != "misc":
        fields.append(("publisher", _latex_escape(venue.get("publisher"))))
    if doi:
        fields.append(("doi", doi))
    if url:
        fields.append(("url", url))

    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    entry = f"@{etype}{{{key},\n{body}\n}}"
    meta = {"pid": pid, "key": key, "etype": etype, "year": year,
            "has_locator": bool(doi or url),
            "verified": bool(_as_str(paper.get("source_verified")).strip())}
    return key, entry, meta


def _validate_bibtex(metas: list[dict]) -> list[str]:
    """The 'controllo in uscita': surface entries that would produce a broken or
    unverifiable citation, rather than emitting them silently."""
    warnings: list[str] = []
    seen: dict[str, str] = {}
    for m in metas:
        if m["key"] in seen:
            warnings.append(f"{m['pid']}: duplicate cite key '{m['key']}' "
                            f"(shared with {seen[m['key']]})")
        else:
            seen[m["key"]] = m["pid"]
        if not m["year"]:
            warnings.append(f"{m['pid']} ({m['key']}): missing year")
        if not m["has_locator"]:
            warnings.append(f"{m['pid']} ({m['key']}): no DOI or URL — unlocatable")
        if not m["verified"]:
            warnings.append(f"{m['pid']} ({m['key']}): source_verified empty "
                            f"— metadata not verified")
    return warnings


def build_bibtex(papers: dict, venues: dict, ids: list[str] | None = None,
                 include_hidden: bool = False) -> tuple[str, list[str]]:
    """Build the .bib text plus a list of validation warnings. `ids` selects a
    subset (in the given order); otherwise every non-hidden paper is included."""
    if ids:
        items = [(pid, papers[pid]) for pid in ids if pid in papers]
    else:
        items = [(pid, p) for pid, p in papers.items()
                 if include_hidden or not p.get("hidden")]
    entries, metas = [], []
    for pid, p in items:
        _key, entry, meta = _bibtex_entry(pid, p, venues)
        entries.append(entry)
        metas.append(meta)
    warnings = _validate_bibtex(metas)
    header = f"% Too Many Papers — BibTeX export, {len(entries)} entr" \
             f"{'y' if len(entries) == 1 else 'ies'}\n\n"
    content = header + "\n\n".join(entries) + ("\n" if entries else "")
    return content, warnings


def regenerate_bibtex_export(papers_data: dict, venues_data: dict) -> None:
    """Rewrite ~/.too-many-papers/exports/library.bib from the whole (non-hidden)
    library. Called automatically after every papers save; must never raise into
    the caller."""
    papers = papers_data.get("papers", {})
    venues = venues_data.get("venues", {})
    content, warnings = build_bibtex(papers, venues)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(BIBTEX_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    n_entries = sum(1 for line in content.splitlines() if line.startswith("@"))
    _log_event("bibtex_exported", entries=n_entries, warnings=len(warnings))


def cmd_export(args):
    """Export papers as BibTeX. Usage:
        export [--format bibtex] [--ids P001,P004] [--report]
    Prints pure .bib to stdout; with --report, appends validation warnings as
    trailing % comment lines (harmless to BibTeX, visible to the caller)."""
    fmt = "bibtex"
    ids = None
    report = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--format" and i + 1 < len(args):
            fmt = args[i + 1].lower(); i += 2; continue
        if a == "--ids" and i + 1 < len(args):
            ids = [x.strip().upper() for x in args[i + 1].split(",") if x.strip()]; i += 2; continue
        if a == "--report":
            report = True; i += 1; continue
        i += 1

    if fmt != "bibtex":
        print(f"ERROR: unsupported format '{fmt}' (supported: bibtex)"); sys.exit(1)

    papers = load_papers().get("papers", {})
    venues = load_venues().get("venues", {})
    content, warnings = build_bibtex(papers, venues, ids=ids, include_hidden=bool(ids))
    out = content
    if report and warnings:
        out += "\n" + "\n".join("% WARN: " + w for w in warnings) + "\n"
    print(out)


# =============================================================================
# Daily briefing (read-only digest of newly discovered papers)
# =============================================================================
#
# One tool does the whole pipeline server-side: rank the user's concepts by
# engagement, discover fresh candidates for the top ones, and write a Markdown
# digest to ~/.too-many-papers/briefings/<date>.md. It is READ-ONLY — it never
# touches the catalog. The user reads the digest and picks what to add.
# Collapsing the old multi-step routine into a single deterministic tool is
# what lets a scheduled run work unattended (one tool to allow, no live LLM
# judgement mid-loop) and produce a file instead of just chat output.

def _briefing_candidate_line(c: dict) -> str:
    title = (c.get("title") or "Untitled").strip()
    year = c.get("year")
    authors = ", ".join(a for a in (c.get("authors") or []) if a)
    if len(authors) > 140:
        authors = authors[:140].rstrip() + "…"
    venue = c.get("venue") or ""
    link = c.get("url") or (f"https://doi.org/{c['doi']}" if c.get("doi") else "") \
        or (f"https://arxiv.org/abs/{c['arxiv_id']}" if c.get("arxiv_id") else "")
    meta = " · ".join(x for x in [str(year) if year else "", venue] if x)
    head = f"- **{title}**" + (f" ({meta})" if meta else "")
    lines = [head]
    if authors:
        lines.append(f"  {authors}")
    if link:
        lines.append(f"  {link}")
    abstract = (c.get("abstract") or "").strip()
    if abstract:
        snippet = abstract if len(abstract) <= 280 else abstract[:280].rstrip() + "…"
        lines.append(f"  {snippet}")
    return "\n".join(lines)


def _render_briefing(the_date, sections) -> str:
    """sections: list of (concept_name, [candidate dicts], error_or_None)."""
    total = sum(len(c) for _, c, _ in sections)
    out = [f"# Paper briefing — {the_date}", ""]
    if total == 0:
        out.append("No new candidates found today across your top concepts.")
        out.append("")
    for name, cands, err in sections:
        out.append(f"## {name}")
        if err:
            out.append(f"_Discovery unavailable: {err}_")
        elif not cands:
            out.append("_No new candidates today._")
        else:
            out.extend(_briefing_candidate_line(c) for c in cands)
        out.append("")
    out.append("---")
    out.append("Reply with which of these you'd like to add to your library.")
    out.append("")
    return "\n".join(out)


def cmd_briefing(args):
    """Generate a read-only daily briefing digest and save it under
    briefings/<date>.md. Usage:
        briefing [--date YYYY-MM-DD] [--concepts N] [--per-concept N] [--year-from Y]
    Never modifies the catalog."""
    the_date = str(date.today())
    n_concepts, per_concept = 3, 6
    year_from = date.today().year
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--date" and i + 1 < len(args):
            the_date = args[i + 1]; i += 2; continue
        if a == "--concepts" and i + 1 < len(args):
            try: n_concepts = max(1, int(args[i + 1]))
            except ValueError: pass
            i += 2; continue
        if a == "--per-concept" and i + 1 < len(args):
            try: per_concept = max(1, int(args[i + 1]))
            except ValueError: pass
            i += 2; continue
        if a == "--year-from" and i + 1 < len(args):
            try: year_from = int(args[i + 1])
            except ValueError: pass
            i += 2; continue
        i += 1

    graph = load_graph()
    nodes = graph.get("nodes", {})
    # Top concepts by engagement, falling back to any concepts if there are no
    # interactions yet (fresh graph).
    ranked = rank_engagement(graph, top_n=50)
    top = [(nid, nodes[nid]) for nid, _ in ranked
           if nid in nodes and nodes[nid].get("type") == "concept"][:n_concepts]
    if not top:
        top = [(nid, n) for nid, n in nodes.items() if n.get("type") == "concept"][:n_concepts]

    sections = []
    for cid, node in top:
        name = node.get("name", cid)
        try:
            res = discover_candidates(concept_id=cid, year_from=year_from, max_results=per_concept)
            sections.append((name, res["new_candidates"][:per_concept], None))
        except Exception as e:
            sections.append((name, [], f"{type(e).__name__}: {e}"))

    md = _render_briefing(the_date, sections)
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFINGS_DIR / f"{the_date}.md"
    path.write_text(md, encoding="utf-8")
    _log_event("briefing_generated", date=str(the_date),
               concepts=len(top), candidates=sum(len(c) for _, c, _ in sections))
    print(f"Briefing saved to {path}\n")
    print(md)


def cmd_briefing_list(args):
    """List saved briefing dates, newest first."""
    if not BRIEFINGS_DIR.exists():
        print("No briefings yet."); return
    files = sorted((p.stem for p in BRIEFINGS_DIR.glob("*.md")), reverse=True)
    if not files:
        print("No briefings yet."); return
    print(f"{len(files)} briefing(s):")
    for d in files:
        print(f"  {d}")


def cmd_briefing_get(args):
    """Print a saved briefing. Usage: briefing-get [YYYY-MM-DD]  (default: latest)."""
    if not BRIEFINGS_DIR.exists():
        print("No briefings yet."); return
    files = sorted((p.stem for p in BRIEFINGS_DIR.glob("*.md")), reverse=True)
    if not files:
        print("No briefings yet."); return
    want = args[0] if args else files[0]
    path = BRIEFINGS_DIR / f"{want}.md"
    if not path.exists():
        print(f"No briefing for '{want}'. Available: {', '.join(files[:10])}"); return
    print(path.read_text(encoding="utf-8"))


COMMANDS = {
    # paper
    "list":            cmd_list,
    "get":             cmd_get,
    "search":          cmd_search,
    "by-concept":      cmd_by_concept,
    "by-author":       cmd_by_author,
    "by-venue":        cmd_by_venue,
    "by-year":         cmd_by_year,
    "outside":         cmd_outside,
    "hidden":          cmd_hidden,
    "next-id":         cmd_next_id,
    "add-paper":       cmd_add_paper,
    "update-paper":    cmd_update_paper,
    "check-duplicates": cmd_check_duplicates,
    "papers-discover": cmd_papers_discover,
    "hide":            cmd_hide,
    "unhide":          cmd_unhide,
    "delete-paper":    cmd_delete_paper,
    "get-citations":   cmd_get_citations,
    "apply-citations": cmd_apply_citations,
    "sync-citations":  cmd_sync_citations,
    "fetch-pdf":       cmd_fetch_pdf,
    "sync-pdfs":       cmd_sync_pdfs,
    "export":          cmd_export,
    "briefing":        cmd_briefing,
    "briefing-list":   cmd_briefing_list,
    "briefing-get":    cmd_briefing_get,
    # venue
    "venue-list":      cmd_venue_list,
    "venue-get":       cmd_venue_get,
    "add-venue":       cmd_add_venue,
    "update-venue":    cmd_update_venue,
    "delete-venue":    cmd_delete_venue,
    # graph
    "graph-status":       cmd_graph_status,
    "graph-node":         cmd_graph_node,
    "graph-nodes":        cmd_graph_nodes,
    "graph-add-node":     cmd_graph_add_node,
    "graph-update-node":  cmd_graph_update_node,
    "graph-remove-node":  cmd_graph_remove_node,
    "graph-add-edge":     cmd_graph_add_edge,
    "graph-remove-edge":  cmd_graph_remove_edge,
    "graph-neighbors":    cmd_graph_neighbors,
    "graph-interact":     cmd_graph_interact,
    "graph-engagement":   cmd_graph_engagement,
    "graph-search":       cmd_graph_search,
    "graph-lint":         cmd_graph_lint,
}

def main():
    _configure_stdio()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.stdout.flush()
        return
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]
    if cmd not in COMMANDS:
        print(f"Unknown command: '{cmd}'")
        print(f"Available commands: {', '.join(sorted(COMMANDS))}")
        sys.stdout.flush()
        sys.exit(1)
    COMMANDS[cmd](args)
    sys.stdout.flush()

if __name__ == "__main__":
    main()

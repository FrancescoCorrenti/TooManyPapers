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

VENUE COMMANDS — READ
----------------------
  venue-list                    -> all venues (ID, name, type)
  venue-get <VID>               -> full record of a venue

VENUE COMMANDS — WRITE
-----------------------
  add-venue <json>              -> adds a venue (ID assigned automatically)
  update-venue <VID> <json>     -> updates fields of an existing venue (partial merge)

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
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

# -- Paths -----------------------------------------------------------------

# _scripts/ is a subfolder of the project root
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent

# Bundled empty seed files, shipped with the plugin and version-controlled.
TEMPLATES_DIR = ROOT_DIR / "_templates"

# Where the user's actual data lives. When running as an installed Claude
# Code / Cowork plugin, .mcp.json sets TOO_MANY_PAPERS_DATA_DIR to
# ${CLAUDE_PLUGIN_DATA} — a directory that persists across plugin updates
# (unlike the plugin's own source tree, which gets wiped and replaced on
# every update/reinstall). Falling back to ROOT_DIR keeps local/dev usage
# (running the script directly, without the plugin's env var) working the
# same as before.
DATA_DIR = Path(os.environ.get("TOO_MANY_PAPERS_DATA_DIR") or ROOT_DIR)

PAPERS_FILE = DATA_DIR / "_papers.json"
VENUES_FILE = DATA_DIR / "_venues.json"
GRAPH_FILE = DATA_DIR / "_graph.json"


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
    with open(PAPERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
PAPER_OPTIONAL_FIELDS = {"url", "hidden", "cites", "cited_by", "cites_unmatched"}
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

    save_papers(data)
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
    print(f"Paper {pid} restored (no longer hidden).")
    print(format_paper(pid, data["papers"][pid], verbose=True))

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
S2_MIN_INTERVAL = float(os.environ.get("S2_MIN_INTERVAL", "1.0"))
_last_s2_request_time = [0.0]

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


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

    if arxiv_id:
        lookup_url = f"{S2_API_BASE}/arXiv:{arxiv_id}?fields=title,externalIds"
    elif doi:
        lookup_url = f"{S2_API_BASE}/DOI:{doi}?fields=title,externalIds"
    else:
        return None, ("No verbatim arXiv ID or DOI found in venue_detail/"
                       "source_verified/url — cannot query Semantic Scholar "
                       "without a real identifier.")

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

    if arxiv_id:
        lookup_url = f"{S2_API_BASE}/arXiv:{arxiv_id}?fields=title,externalIds"
    elif doi:
        lookup_url = f"{S2_API_BASE}/DOI:{doi}?fields=title,externalIds"
    else:
        return None, ("No verbatim arXiv ID or DOI found in venue_detail/"
                       "source_verified/url — cannot query Semantic Scholar "
                       "without a real identifier.")

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
    print(f"Venue {vid} updated.")
    print(format_venue(vid, data["venues"][vid], verbose=True))

# -- Graph helpers ---------------------------------------------------------

GRAPH_NODE_TYPES = {"concept", "project", "endpoint", "idea", "pool"}
GRAPH_EDGE_TYPES = {"connected_to", "uses_concept", "part_of", "inspired_by",
                    "relevant_to", "derived_from", "enables"}
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
}

NODE_OPTIONAL_FIELDS = {
    "concept": {"description"},
    "project": {"description"},
    "endpoint": {"description"},
    "idea": {"description", "source"},
    "pool": {"description"},
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
    """Overview of graph: node counts by type, edges, interactions."""
    graph = load_graph()
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])
    interactions = graph.get("interactions", [])

    type_counts = {}
    for n in nodes.values():
        t = n.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("GRAPH STATUS")
    print(SEP_HEAVY * 50)
    print(f"Total nodes: {len(nodes)}")
    for t, c in sorted(type_counts.items()):
        print(f"  {t:<15} {c}")
    print(f"Total edges: {len(edges)}")
    print(f"Total interactions: {len(interactions)}")
    if interactions:
        latest = max(i.get("date", "") for i in interactions)
        print(f"Latest interaction: {latest}")


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
    print(f"Removed node {node_id} ({removed_node.get('name','')}) and {edges_removed} edges.")


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
    save_graph(graph)

    src_name = src_node.get("name", "")[:30]
    tgt_name = tgt_node.get("name", "")[:30]
    print(f"Edge added: {src} ({src_name}) -> {tgt} ({tgt_name}) [{edge_type}]")


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

    name = node.get("name", "")[:40]
    print(f"Interaction logged: {node_id} ({name}) | {int_type} | w={weight} | {date.today()}")


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
    interactions = graph.get("interactions", [])

    if not interactions:
        print("No interactions logged."); return

    today = date.today()
    scores = {}  # node_id -> {score, last_date, recent_count, older_count}

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
        contribution = w * decay

        if nid not in scores:
            scores[nid] = {"score": 0.0, "last_date": d, "recent": 0, "older": 0}
        scores[nid]["score"] += contribution
        if d > scores[nid]["last_date"]:
            scores[nid]["last_date"] = d
        if weeks <= 2:
            scores[nid]["recent"] += 1
        else:
            scores[nid]["older"] += 1

    # Sort and display
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)[:top_n]

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
    "hide":            cmd_hide,
    "unhide":          cmd_unhide,
    "get-citations":   cmd_get_citations,
    "apply-citations": cmd_apply_citations,
    "sync-citations":  cmd_sync_citations,
    # venue
    "venue-list":      cmd_venue_list,
    "venue-get":       cmd_venue_get,
    "add-venue":       cmd_add_venue,
    "update-venue":    cmd_update_venue,
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

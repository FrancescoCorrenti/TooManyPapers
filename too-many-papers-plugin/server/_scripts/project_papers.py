"""Project/paper associations, review state and lossless v3 migration."""
import copy
import hashlib
import json
from datetime import date


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context(project, paper):
    return digest({
        "project": {k: project.get(k) for k in ("name", "description")},
        "paper": {k: paper.get(k) for k in ("title", "abstract", "notes", "file", "url", "source_verified")},
    })


def next_idea(nodes):
    i = 1
    while f"IDEA-{i:03d}" in nodes:
        i += 1
    return f"IDEA-{i:03d}"


def review_state(idea, graph, papers):
    project_id, paper_id = idea.get("project_id"), idea.get("paper_id")
    if not project_id or not paper_id:
        legacy = idea.get("legacy")
        if not legacy:
            return None
        resolved_projects = set()
        for iid in legacy.get("resolved_by", []):
            analyzed = graph.get("nodes", {}).get(iid, {})
            if analyzed.get("project_id") and analyzed.get("paper_id") and review_state(analyzed, graph, papers) == "current":
                resolved_projects.add(analyzed["project_id"])
        if resolved_projects and set(legacy.get("project_ids", [])) <= resolved_projects:
            return "incorporated"
        return "needs_analysis"
    project = graph.get("nodes", {}).get(project_id)
    paper = papers.get(paper_id)
    linked = any(e.get("src") == paper_id and e.get("tgt") == project_id
                 and e.get("type") == "relevant_to"
                 and graph.get("nodes", {}).get(e.get("idea_id")) is idea
                 for e in graph.get("edges", []))
    if not project or not paper or not linked:
        return "detached"
    if not idea.get("analyzed_context"):
        return "needs_analysis"
    if (idea["analyzed_context"] != context(project, paper)
            or idea.get("analysis_digest") != digest(idea.get("description", ""))):
        return "stale"
    return "current"


def migrate(graph, papers):
    """Keep original IDs/text and archive removed topology; never invent relevance."""
    if graph.get("_meta", {}).get("version") == "3.0":
        return False
    nodes = graph.setdefault("nodes", {})
    old_nodes = copy.deepcopy(nodes)
    old_edges = copy.deepcopy(graph.get("edges", []))
    legacy_ids = {nid for nid, n in nodes.items() if n.get("type") in {"waypoint", "endpoint"}}
    for nid, n in nodes.items():
        if nid not in legacy_ids and n.get("type") != "idea":
            continue
        # Walk only old work/idea nodes; projects and papers are terminal peers.
        visited, todo, projects, candidates = set(), [nid], set(), set()
        while todo:
            cur = todo.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for e in old_edges:
                other = e.get("tgt") if e.get("src") == cur else e.get("src") if e.get("tgt") == cur else None
                if other in papers:
                    candidates.add(other)
                elif old_nodes.get(other, {}).get("type") == "project":
                    projects.add(other)
                elif other in legacy_ids or old_nodes.get(other, {}).get("type") == "idea":
                    todo.append(other)
        if nid in legacy_ids or projects:
            n["legacy"] = {"original": old_nodes[nid], "project_ids": sorted(projects),
                           "candidate_paper_ids": sorted(candidates), "resolved_by": [],
                           "connections": [e for e in old_edges if nid in (e.get("src"), e.get("tgt"))]}
        if nid in legacy_ids:
            n.update(type="idea", status="open", created=n.get("created") or str(date.today()))
    kept, archived = [], []
    for e in old_edges:
        a, b = e.get("src"), e.get("tgt")
        types = {nodes.get(a, {}).get("type"), nodes.get(b, {}).get("type")}
        if "project" in types:
            project = a if nodes.get(a, {}).get("type") == "project" else b
            paper = b if project == a else a
            if paper in papers:
                edge = {"src": paper, "tgt": project, "type": "relevant_to"}
                if edge not in kept:
                    kept.append(edge)
                continue
            archived.append(e)
        elif e.get("type") == "leads_to" or ("idea" in types and a not in papers and b not in papers):
            archived.append(e)
        else:
            kept.append(e)
    graph["edges"] = kept
    graph.setdefault("_meta", {}).update(version="3.0", migrated_on=str(date.today()))
    graph["_meta"]["legacy_edges"] = archived
    for e in list(kept):
        if e["type"] == "relevant_to" and e["src"] in papers and nodes.get(e["tgt"], {}).get("type") == "project":
            iid = next_idea(nodes)
            nodes[iid] = {"type": "idea", "name": f"Relevance: {papers[e['src']].get('title', e['src'])}",
                          "status": "open", "created": str(date.today()), "description": "",
                          "project_id": e["tgt"], "paper_id": e["src"]}
            e["idea_id"] = iid
            kept.append({"src": iid, "tgt": e["src"], "type": "inspired_by"})
    return True


def association(graph, papers, project_id, paper_id, payload):
    nodes = graph.setdefault("nodes", {})
    project, paper = nodes.get(project_id), papers.get(paper_id)
    if not project or project.get("type") != "project" or paper is None:
        raise ValueError("An existing project and paper are required.")
    for key in ("name", "relevance", "relevant_summary", "evidence", "limitations"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"{key} must be non-empty: read the paper and supply a grounded analysis.")
    if payload.get("context_token") != context(project, paper):
        raise ValueError("Context changed or missing. Call graph_project_context, read the sources, and retry with its context_token.")
    legacy_ids = payload.get("legacy_idea_ids", [])
    if not isinstance(legacy_ids, list) or any(i not in nodes or not nodes[i].get("legacy") for i in legacy_ids):
        raise ValueError("legacy_idea_ids must identify migrated ideas.")
    if any(nodes[i]['legacy'].get('project_ids') and project_id not in nodes[i]['legacy']['project_ids'] for i in legacy_ids):
        raise ValueError("A migrated idea must be analyzed for its originating project.")
    edges = graph.setdefault("edges", [])
    existing = next((e for e in edges if e.get("src") == paper_id and e.get("tgt") == project_id and e.get("type") == "relevant_to"), None)
    iid = existing.get("idea_id") if existing else None
    if iid not in nodes:
        iid = next((i for i, n in nodes.items() if n.get("project_id") == project_id
                    and n.get("paper_id") == paper_id), None) or next_idea(nodes)
    idea = nodes.setdefault(iid, {"type": "idea", "status": "open", "created": str(date.today())})
    if idea.get("description"):
        idea.setdefault("analysis_history", []).append({k: copy.deepcopy(idea.get(k)) for k in
            ("description", "analyzed_on", "analyzed_context", "analysis_digest")})
    description = "\n\n".join(f"## {label}\n{payload[key].strip()}" for label, key in
        (("Project relevance", "relevance"), ("Relevant findings", "relevant_summary"),
         ("Evidence", "evidence"), ("Limitations", "limitations")))
    idea.update(name=payload["name"].strip(), description=description, project_id=project_id,
                paper_id=paper_id, analyzed_context=payload["context_token"],
                analysis_digest=digest(description), analyzed_on=str(date.today()), source="project_paper")
    idea["review_state"] = "current"
    if existing is None:
        edges.append({"src": paper_id, "tgt": project_id, "type": "relevant_to", "idea_id": iid})
    else:
        existing["idea_id"] = iid
    if not any(e.get("src") == iid and e.get("tgt") == paper_id and e.get("type") == "inspired_by" for e in edges):
        edges.append({"src": iid, "tgt": paper_id, "type": "inspired_by"})
    for lid in legacy_ids:
        resolved = nodes[lid]["legacy"].setdefault("resolved_by", [])
        if iid not in resolved:
            resolved.append(iid)
    return iid

# MCP Tools Reference — Full Detail

## Paper tools (15)

| Tool | Description |
|------|-------------|
| `papers_list` | List all papers |
| `papers_get` | Get full paper card by ID |
| `papers_search` | Fuzzy search on title and authors |
| `papers_by_concept` | Papers tagged with a concept |
| `papers_by_author` | Papers by author surname |
| `papers_by_venue` | Papers published in a venue |
| `papers_by_year` | Papers by publication year |
| `papers_outside` | Papers outside comfort zone |
| `papers_hidden` | Hidden papers |
| `papers_next_id` | Next available paper ID |
| `papers_add` | Add a new paper (with validation) |
| `papers_update` | Update paper fields (merge patch) |
| `papers_check_duplicates` | Check candidates against existing catalog |
| `papers_hide` | Hide a paper from default views |
| `papers_unhide` | Restore a hidden paper |

## Graph tools (13)

| Tool | Description |
|------|-------------|
| `graph_status` | Overview: node/edge/interaction counts |
| `graph_node` | Get a node with all its edges and recent interactions |
| `graph_nodes` | List nodes, optionally filtered by type |
| `graph_add_node` | Add a node (concept/project/endpoint/idea/pool) |
| `graph_update_node` | Update node fields |
| `graph_remove_node` | Remove a node and all its edges |
| `graph_add_edge` | Add a typed edge between nodes |
| `graph_remove_edge` | Remove edges between nodes |
| `graph_neighbors` | BFS traversal from a node (configurable depth) |
| `graph_path` | Find shortest path between two nodes |
| `graph_interact` | Log an interaction (engagement tracking) |
| `graph_engagement` | Compute engagement scores with decay |
| `graph_search` | Full-text search across nodes and papers |

## Citation tools (3)

| Tool | Description |
|------|-------------|
| `citations_get` | Fetch real citations from Semantic Scholar (read-only) |
| `citations_apply` | Fetch and save citation links to paper |
| `citations_sync` | Sync citations for all papers |

## Venue tools (4)

| Tool | Description |
|------|-------------|
| `venues_list` | List all venues |
| `venues_get` | Get venue details |
| `venues_add` | Add a new venue |
| `venues_update` | Update venue fields |

## Node types

| Node type | What it represents |
|-----------|-------------------|
| `concept` | A research area you care about |
| `project` | An active research project with goals |
| `endpoint` | A specific milestone within a project |
| `idea` | A concrete idea connected to a project |
| `pool` | A transversal idea that spans projects |

## Edge types

| Edge type | Connects |
|-----------|----------|
| `connected_to` | concept <> concept |
| `uses_concept` | project > concept |
| `part_of` | endpoint/idea > project |
| `inspired_by` | idea > paper |
| `relevant_to` | paper > project |
| `enables` | concept > concept (directional) |
| `derived_from` | any > any |

## CLI usage (without MCP)

`papers_api.py` also works standalone from the terminal:

```bash
python server/_scripts/papers_api.py list
python server/_scripts/papers_api.py search "attention mechanism"
python server/_scripts/papers_api.py add-paper '{"title": "...", "authors": [...], ...}'
python server/_scripts/papers_api.py graph-status
python server/_scripts/papers_api.py graph-neighbors C001 --depth 2
python server/_scripts/papers_api.py graph-path C003 PROJ-FCD
python server/_scripts/papers_api.py graph-engagement --top 5
python server/_scripts/papers_api.py graph-search "segmentation"
python server/_scripts/papers_api.py --help
```

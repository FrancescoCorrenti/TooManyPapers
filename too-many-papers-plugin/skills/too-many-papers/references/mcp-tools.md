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

## Graph tools (17)

| Tool | Description |
|------|-------------|
| `graph_status` | Overview: node/edge/interaction counts |
| `graph_node` | Get a node with all its edges and recent interactions |
| `graph_nodes` | List nodes, optionally filtered by type |
| `graph_add_concept` | Add a concept node — `name`, `area`, `description?` |
| `graph_add_project` | Add a project node — `name`, `status`, `description?` |
| `graph_add_endpoint` | Add an endpoint node — `name`, `status`, `description?` |
| `graph_add_idea` | Add an idea node — `name`, `status`, `created`, `description?`, `source?` |
| `graph_add_pool` | Add a pool node — `name`, `created`, `description?` |
| `graph_update_node` | Update node fields (merge patch; rejects fields not valid for that node's type) |
| `graph_remove_node` | Remove a node and all its edges |
| `graph_add_edge` | Add a typed edge between nodes |
| `graph_remove_edge` | Remove edges between nodes |
| `graph_neighbors` | BFS traversal from a node (configurable depth) |
| `graph_path` | Find shortest path between two nodes |
| `graph_interact` | Log an interaction (engagement tracking) |
| `graph_engagement` | Compute engagement scores with decay |
| `graph_search` | Full-text search across nodes and papers |

There is one typed `graph_add_*` tool per node type instead of a single generic
`graph_add_node(type, payload)` — each tool's parameters are exactly that
type's real fields (required ones as required parameters, `description` and
other optional fields defaulting to empty). This means a field that doesn't
exist for that node type (e.g. a made-up "goal" on a project — the right
field is `description`) isn't just rejected by validation, it isn't part of
the tool's schema at all.

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
| `connected_to
# MCP Tools Reference — Full Detail

## Paper tools (16)

| Tool | Description |
|------|-------------|
| `papers_list` | List all papers |
| `papers_get` | Get full paper card by ID |
| `papers_search` | Fuzzy search on title and authors (local catalog only) |
| `papers_by_concept` | Papers tagged with a concept |
| `papers_by_author` | Papers by author surname |
| `papers_by_venue` | Papers published in a venue |
| `papers_by_year` | Papers by publication year |
| `papers_outside` | Papers outside comfort zone |
| `papers_hidden` | Hidden papers |
| `papers_next_id` | Next available paper ID |
| `papers_discover` | Search arXiv/Semantic Scholar/OpenAlex for new papers (+ citation-based expansion), deduplicated across providers and against the catalog. The only sanctioned way to find new papers — never use WebSearch/WebFetch for this. |
| `papers_add` | Add a new paper (with validation) |
| `papers_update` | Update paper fields (merge patch) |
| `papers_check_duplicates` | Check candidates against existing catalog |
| `papers_hide` | Hide a paper from default views |
| `papers_unhide` | Restore a hidden paper |

### `papers_discover` parameters

| Param | Type | Description |
|-------|------|-------------|
| `query` | string | Topic/keywords. Optional if `concept_id` or `seed_paper_ids` alone give enough context. |
| `concept_id` | string | Graph concept ID (e.g. `C003`) — its name/area/description are appended to the query. |
| `seed_paper_ids` | list of strings | Catalog paper IDs whose Semantic Scholar references are pulled in as extra candidates — this is the citation-search integration. |
| `providers` | list of strings | Subset of `arxiv`, `semantic_scholar`, `openalex` (default: all three). |
| `year_from` | int | Minimum publication year. |
| `max_results` | int | Per-provider cap before merge/dedup, 1-50 (default 10). |

Returns `new_candidates` (ready for `papers_add`), `already_in_catalog` (titles dropped as duplicates), and `errors` (per-provider failures — e.g. rate limits — reported plainly, never silently swallowed or papered over with invented data).

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
`graph_add_node(type, payload)` — each tool's parameters 
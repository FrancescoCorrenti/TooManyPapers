/**
 * Too Many Papers - Mini Server
 * Run with: node paper-library-server.js
 * Then open: http://localhost:3737
 *
 * Reads _papers.json, _venues.json, _graph.json
 * Exposes papers via API and updates _papers.json on "read" toggle.
 */

const http = require('http');
const fs   = require('fs');
const path = require('path');
const os   = require('os');
const { spawnSync } = require('child_process');

// The BibTeX exporter is the Python one in papers_api.py — the single source
// of truth, with its output validation. The web UI shells out to it rather
// than reimplementing (and drifting from) the generator here.
const SERVER_DIR = path.join(__dirname, '..', 'server');
function runBibtexExport(ids) {
  const args = ['run', '--directory', SERVER_DIR, '_scripts/papers_api.py',
                'export', '--format', 'bibtex'];
  if (ids) args.push('--ids', ids);
  const r = spawnSync('uv', args, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  if (r.error) return { error: 'Could not run the exporter (is uv installed?): ' + r.error.message };
  if (r.status !== 0) return { error: 'Exporter failed: ' + String(r.stderr || '').slice(0, 500) };
  return { content: r.stdout };
}

const PORT        = parseInt(process.env.PORT, 10) || 3737;
// Data always lives at ~/.too-many-papers, unconditionally — independent of
// OS, host, or plugin install location. Some hosts re-provision the plugin's
// own source tree fresh every session (wiping anything under it) and some
// don't expand placeholder env vars like ${CLAUDE_PLUGIN_DATA} at all, so any
// data dir derived from the plugin's environment can silently reset between
// sessions. A fixed path under the user's home directory is the only
// location guaranteed to survive across sessions, hosts, and plugin updates.
function resolveDataDir() {
  return path.join(os.homedir(), '.too-many-papers');
}
const DATA_DIR    = resolveDataDir();
const PAPERS_FILE = path.join(DATA_DIR, '_papers.json');
const VENUES_FILE = path.join(DATA_DIR, '_venues.json');
const GRAPH_FILE  = path.join(DATA_DIR, '_graph.json');
const HTML_FILE   = path.join(__dirname, 'paper-library.html');

// Mirrors papers_api.py's _ensure_data_files(): create DATA_DIR and seed it
// from the bundled templates the first time it's used. Never overwrites
// existing data.
function ensureDataFiles() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const templatesDir = path.join(__dirname, '..', 'server', '_templates');
  for (const name of ['_papers.json', '_venues.json', '_graph.json']) {
    const target = path.join(DATA_DIR, name);
    if (!fs.existsSync(target)) {
      const template = path.join(templatesDir, name);
      fs.writeFileSync(target, fs.existsSync(template) ? fs.readFileSync(template) : '{}');
    }
  }
  const logFile = path.join(DATA_DIR, '_log.jsonl');
  if (!fs.existsSync(logFile)) fs.writeFileSync(logFile, '');
  const pdfsDir = path.join(DATA_DIR, 'pdfs');
  fs.mkdirSync(pdfsDir, { recursive: true });
}
ensureDataFiles();

// Directories to exclude from file search
const SKIP_DIRS = new Set(['node_modules', '.git', '_scripts']);

function findFileSync(dir, name) {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        const found = findFileSync(path.join(dir, entry.name), name);
        if (found) return found;
      } else if (entry.name === name) {
        return path.join(dir, entry.name);
      }
    }
  } catch(e) {}
  return null;
}

// Resolves a paper's `file` field to an absolute path on disk.
//
// `file` is normally a path relative to DATA_DIR (e.g. "pdfs/P001.pdf",
// written by papers_api.py's automatic PDF fetching — see fetch-pdf/
// sync-pdfs). Try that exact location first. Fall back to a recursive
// filename search — under DATA_DIR, then under the plugin's own source
// tree — for the older/manual workflow where someone just typed a bare
// filename and dropped the PDF somewhere inside the plugin folder rather
// than through the automatic fetcher. Returns null if nothing is found.
function resolvePdfPath(file) {
  if (!file) return null;
  const direct = path.join(DATA_DIR, file);
  if (fs.existsSync(direct) && fs.statSync(direct).isFile()) return direct;

  const basename = path.basename(file);
  return findFileSync(DATA_DIR, basename) || findFileSync(path.join(__dirname, '..'), basename);
}

// "Ghost" papers: titles that show up in cites_unmatched (real references
// Semantic Scholar found for a paper, but that aren't themselves in the
// local catalog) — see papers_api.py's compute_citation_links/_apply_links.
// Deduped by normalized title so the same missing paper cited by several
// local papers collapses into one entry instead of one per citer.
function normalizeTitle(t) {
  return (t || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function computeGhostPapers(papers) {
  const ghosts = new Map(); // normalized title -> {key, title, year, citedBy}
  for (const [id, p] of Object.entries(papers)) {
    (p.cites_unmatched || []).forEach(u => {
      const title = (u.title || '').trim();
      if (!title) return;
      const key = normalizeTitle(title);
      if (!ghosts.has(key)) ghosts.set(key, { key, title, year: u.year || null, citedBy: [] });
      const g = ghosts.get(key);
      if (!g.year && u.year) g.year = u.year;
      if (!g.citedBy.some(c => c.id === id)) g.citedBy.push({ id, title: p.title || id });
    });
  }
  return [...ghosts.values()].sort((a, b) => a.title.localeCompare(b.title));
}

function loadData() {
  const papersDb = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const venuesDb = JSON.parse(fs.readFileSync(VENUES_FILE, 'utf8'));
  const graphDb  = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));

  const venues = venuesDb.venues || {};

  // Extract concepts from graph nodes where type=concept
  const concepts = {};
  for (const [id, node] of Object.entries(graphDb.nodes || {})) {
    if (node.type === 'concept') {
      concepts[id] = { name: node.name, area: node.area || '' };
    }
  }

  const papers = papersDb.papers || {};

  const result = Object.entries(papers).map(([id, p]) => {
    const venue = venues[p.venue_id] || {};
    const conceptList = (p.concepts || []).map(cid => ({
      id:   cid,
      name: (concepts[cid] && concepts[cid].name) || cid,
      area: (concepts[cid] && concepts[cid].area) || '',
    }));

    const rawAuthors = Array.isArray(p.authors)
      ? p.authors.filter(a => a && a !== '[non disponibile]').join(', ')
      : (p.authors || '');

    const venueName = venue.name || p.venue_detail || p.venue_id || '';
    const venueType = venue.type || (p.venue_detail ? 'preprint' : '');

    // Cites/cited_by: only IDs actually present in the local catalog (never
    // unresolved external references — those live in cites_unmatched and
    // are never exposed as clickable papers).
    const cites = (p.cites || [])
      .filter(cid => papers[cid])
      .map(cid => ({ id: cid, title: papers[cid].title || cid }));
    const citedBy = (p.cited_by || [])
      .filter(cid => papers[cid])
      .map(cid => ({ id: cid, title: papers[cid].title || cid }));

    return {
      id,
      titolo:      p.title            || '',
      autori:      rawAuthors,
      annoPub:     p.year             || '',
      dataScop:    p.discovered       || '',
      venueId:     p.venue_id         || '',
      venueDetail: p.venue_detail     || '',
      venueName:   venueName,
      venueType:   venueType,
      source:      p.source_verified  || '',
      url:         p.url             || '',
      concepts:    conceptList,
      file:        p.file             || '',
      fileExists:  !!resolvePdfPath(p.file),
      outsideZone: p.outside_zone     || false,
      notes:       p.notes            || '',
      read:        p.read             || false,
      hidden:      p.hidden           || false,
      cites:       cites,
      citedBy:     citedBy,
      pdfNotes:    Array.isArray(p.pdf_notes) ? p.pdf_notes : [],
    };
  });

  result.sort(function(a, b) {
    if (b.dataScop !== a.dataScop) return b.dataScop.localeCompare(a.dataScop);
    return a.id.localeCompare(b.id);
  });

  return { papers: result, venues: venues, concepts: concepts, ghosts: computeGhostPapers(papers) };
}

function loadGraph() {
  const papersDb = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const venuesDb = JSON.parse(fs.readFileSync(VENUES_FILE, 'utf8'));
  const graphDb  = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));

  const papers = papersDb.papers || {};
  const venues = venuesDb.venues || {};
  const graphNodes = graphDb.nodes || {};
  const graphEdges = graphDb.edges || [];

  const nodes = {};
  for (const [id, node] of Object.entries(graphNodes)) {
    nodes[id] = Object.assign({ id }, node);
  }
  for (const [id, p] of Object.entries(papers)) {
    const venue = venues[p.venue_id] || {};
    const authors = Array.isArray(p.authors)
      ? p.authors.filter(a => a && a !== '[non disponibile]').join(', ')
      : (p.authors || '');
    nodes[id] = {
      id,
      type:     'paper',
      title:    p.title    || '',
      year:     p.year     || '',
      concepts: p.concepts || [],
      authors:  authors,
      venue:    venue.name || p.venue_detail || p.venue_id || '',
    };
  }

  const edges = graphEdges.map(e => ({
    from: e.src, to: e.tgt, type: e.type, note: e.note || '',
  }));

  // Ghost nodes: papers cited by something in the library but absent from
  // it, deduped by normalized title so the same missing paper cited by
  // several local papers becomes one node, not one per citer. Excluded from
  // the graph's default type filter client-side (opt-in "Missing" toggle).
  const ghostIdByKey = new Map();
  function ghostNodeId(key) {
    if (!ghostIdByKey.has(key)) ghostIdByKey.set(key, 'GHOST-' + String(ghostIdByKey.size + 1).padStart(3, '0'));
    return ghostIdByKey.get(key);
  }

  for (const [id, p] of Object.entries(papers)) {
    (p.cites || []).forEach(cid => {
      if (papers[cid]) edges.push({ from: id, to: cid, type: 'cites' });
    });
    (p.concepts || []).forEach(cid => {
      if (nodes[cid]) edges.push({ from: id, to: cid, type: 'concept_tag' });
    });
    (p.cites_unmatched || []).forEach(u => {
      const title = (u.title || '').trim();
      if (!title) return;
      const gid = ghostNodeId(normalizeTitle(title));
      if (!nodes[gid]) nodes[gid] = { id: gid, type: 'ghost', title, year: u.year || '' };
      edges.push({ from: id, to: gid, type: 'cites' });
    });
  }

  return { nodes, edges };
}

function toggleRead(paperId) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return false;
  papers[paperId].read = !papers[paperId].read;
  raw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return true;
}

function toggleHidden(paperId) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return false;
  papers[paperId].hidden = !papers[paperId].hidden;
  raw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return true;
}

// Permanent delete (not the soft hide above) — mirrors papers_api.py's
// cmd_delete_paper: pop the paper and scrub its ID out of every other
// paper's cites/cited_by/cites_unmatched so no dangling references remain.
function deletePaper(paperId) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };
  delete papers[paperId];
  for (const other of Object.values(papers)) {
    for (const field of ['cites', 'cited_by', 'cites_unmatched']) {
      if (Array.isArray(other[field])) {
        other[field] = other[field].filter(v => v !== paperId);
      }
    }
  }
  raw.papers = papers;
  raw._meta = raw._meta || {};
  raw._meta.total_papers = Object.keys(papers).length;
  raw._meta.last_updated = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

// Permanent delete of any non-paper graph node (concept/project/endpoint/
// idea/pool/note) — mirrors papers_api.py's cmd_graph_remove_node: pop the
// node and drop every edge that touches it.
function deleteGraphNode(nodeId) {
  const raw   = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const nodes = raw.nodes || {};
  if (!nodes[nodeId]) return { error: `Node '${nodeId}' not found.` };
  delete nodes[nodeId];
  raw.nodes = nodes;
  raw.edges = (raw.edges || []).filter(e => e.src !== nodeId && e.tgt !== nodeId);
  fs.writeFileSync(GRAPH_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

// Fields a paper can be edited through from the UI. Deliberately excludes
// cites/cited_by/cites_unmatched/pdf_source/pdf_status/read/hidden — those
// are system-managed (citation sync, PDF fetch, dedicated toggle buttons)
// and editing them by hand here would fight with that automation instead of
// complementing it.
const PAPER_EDITABLE_FIELDS = new Set([
  'title', 'authors', 'year', 'discovered', 'venue_id', 'venue_detail',
  'source_verified', 'concepts', 'file', 'outside_zone', 'notes', 'url',
]);

// PDF notes are first-class graph citizens: each one is a `note` node
// (mirrors papers_api.py's NODE_REQUIRED_FIELDS/NODE_OPTIONAL_FIELDS for
// that type), linked to its paper via an `annotates` edge — so notes show
// up in the Graph view, the Notes tab, and "linked to" filters like any
// other node, not as a dead-end field buried inside the paper JSON.
// `pdf_notes` on the paper is kept as a small read-through cache (same IDs
// as the graph nodes) purely so the inline PDF reader can render its notes
// list without a second round trip to /api/graph.
function nextNoteId(nodes) {
  let max = 0;
  for (const id of Object.keys(nodes)) {
    const m = /^NOTE-(\d+)$/.exec(id);
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  return 'NOTE-' + String(max + 1).padStart(3, '0');
}

function addPdfNote(paperId, page, quote, text) {
  const papersRaw = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers    = papersRaw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };

  quote = quote ? String(quote).trim() : '';
  text  = text  ? String(text).trim()  : '';
  if (!quote && !text) return { error: 'Note must have a quote or text.' };

  const graphRaw = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const nodes    = graphRaw.nodes || {};
  const edges    = graphRaw.edges || (graphRaw.edges = []);

  const noteId = nextNoteId(nodes);
  const created = new Date().toISOString();
  const nameSource = quote || text;
  const name = nameSource.length > 60 ? nameSource.slice(0, 60) + '…' : nameSource;
  const pageNum = page ? (parseInt(page, 10) || null) : null;

  const nodePayload = { name, created, type: 'note' };
  if (quote) nodePayload.quote = quote;
  if (text) nodePayload.text = text;
  if (pageNum) nodePayload.page = pageNum;
  nodes[noteId] = nodePayload;
  edges.push({ src: noteId, tgt: paperId, type: 'annotates' });
  graphRaw.nodes = nodes;
  fs.writeFileSync(GRAPH_FILE, JSON.stringify(graphRaw, null, 2), 'utf8');

  papers[paperId].pdf_notes = papers[paperId].pdf_notes || [];
  papers[paperId].pdf_notes.push({ id: noteId, page: pageNum, quote, text, created });
  papersRaw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(papersRaw, null, 2), 'utf8');

  return { ok: true, noteId };
}

function deletePdfNote(paperId, noteId) {
  const papersRaw = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers    = papersRaw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };
  const before = (papers[paperId].pdf_notes || []).length;
  papers[paperId].pdf_notes = (papers[paperId].pdf_notes || []).filter(n => n.id !== noteId);
  if (papers[paperId].pdf_notes.length === before) return { error: `Note '${noteId}' not found.` };
  papersRaw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(papersRaw, null, 2), 'utf8');

  const graphRaw = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const nodes    = graphRaw.nodes || {};
  if (nodes[noteId]) {
    delete nodes[noteId];
    graphRaw.nodes = nodes;
    graphRaw.edges = (graphRaw.edges || []).filter(e => e.src !== noteId && e.tgt !== noteId);
    fs.writeFileSync(GRAPH_FILE, JSON.stringify(graphRaw, null, 2), 'utf8');
  }
  return { ok: true };
}

function updatePaperFields(paperId, patch) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };

  const unknown = Object.keys(patch).filter(k => !PAPER_EDITABLE_FIELDS.has(k));
  if (unknown.length) {
    return { error: `Unrecognized/non-editable fields: ${unknown.join(', ')}. ` +
      `Editable fields: ${[...PAPER_EDITABLE_FIELDS].join(', ')}.` };
  }
  if ('title' in patch && (!patch.title || !String(patch.title).trim())) {
    return { error: "Field 'title' cannot be empty." };
  }
  if ('authors' in patch && (!Array.isArray(patch.authors) || patch.authors.length === 0)) {
    return { error: "Field 'authors' must be a non-empty list." };
  }

  Object.assign(papers[paperId], patch);
  raw.papers = papers;
  raw._meta = raw._meta || {};
  raw._meta.last_updated = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

// Mirrors papers_api.py's NODE_REQUIRED_FIELDS / NODE_OPTIONAL_FIELDS —
// keep in sync if the schema there changes.
const NODE_REQUIRED_FIELDS = {
  concept:  ['name', 'area'],
  project:  ['name', 'status'],
  endpoint: ['name', 'status'],
  idea:     ['name', 'status', 'created'],
  pool:     ['name', 'created'],
  note:     ['name', 'created'],
};
const NODE_OPTIONAL_FIELDS = {
  concept:  ['description', 'color'],
  project:  ['description'],
  endpoint: ['description'],
  idea:     ['description', 'source'],
  pool:     ['description'],
  note:     ['quote', 'text', 'page'],
};

function updateGraphNodeFields(nodeId, patch) {
  const raw   = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const nodes = raw.nodes || {};
  if (!nodes[nodeId]) return { error: `Node '${nodeId}' not found.` };

  const type = nodes[nodeId].type;
  const allowed = new Set([...(NODE_REQUIRED_FIELDS[type] || []), ...(NODE_OPTIONAL_FIELDS[type] || [])]);
  const unknown = Object.keys(patch).filter(k => !allowed.has(k));
  if (unknown.length) {
    return { error: `Unrecognized fields for type '${type}': ${unknown.join(', ')}. ` +
      `Allowed: ${[...allowed].join(', ')}.` };
  }
  for (const field of NODE_REQUIRED_FIELDS[type] || []) {
    if (field in patch && !String(patch[field] || '').trim()) {
      return { error: `Field '${field}' cannot be empty.` };
    }
  }

  Object.assign(nodes[nodeId], patch);
  raw.nodes = nodes;
  fs.writeFileSync(GRAPH_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

// Mirrors papers_api.py's GRAPH_EDGE_TYPES.
const GRAPH_EDGE_TYPES = new Set([
  'connected_to', 'uses_concept', 'part_of', 'inspired_by',
  'relevant_to', 'derived_from', 'enables', 'annotates',
]);

// Connections (edges) are editable directly from the edit modal for any
// node — including papers, which can be an edge endpoint too (e.g. an idea
// `inspired_by` a paper) even though they live in a separate JSON file.
// `src`/`tgt` may reference either a graph node or a paper ID; we don't
// validate that the ID exists here since either file could contain it and
// the modal's own node/paper picker already only offers real IDs.
function addEdge(src, tgt, type, note) {
  src = String(src || '').trim().toUpperCase();
  tgt = String(tgt || '').trim().toUpperCase();
  if (!src || !tgt) return { error: 'Both ends of the connection are required.' };
  if (src === tgt) return { error: 'A node cannot connect to itself.' };
  if (!GRAPH_EDGE_TYPES.has(type)) {
    return { error: `Unrecognized edge type '${type}'. Allowed: ${[...GRAPH_EDGE_TYPES].join(', ')}.` };
  }
  const raw = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const edges = raw.edges || (raw.edges = []);
  if (edges.some(e => e.src === src && e.tgt === tgt && e.type === type)) {
    return { error: 'That connection already exists.' };
  }
  const edge = { src, tgt, type };
  if (note && String(note).trim()) edge.note = String(note).trim();
  edges.push(edge);
  fs.writeFileSync(GRAPH_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

function deleteEdge(src, tgt, type) {
  src = String(src || '').trim().toUpperCase();
  tgt = String(tgt || '').trim().toUpperCase();
  const raw = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));
  const edges = raw.edges || [];
  const before = edges.length;
  raw.edges = edges.filter(e => !(e.src === src && e.tgt === tgt && e.type === type));
  if (raw.edges.length === before) return { error: 'Connection not found.' };
  fs.writeFileSync(GRAPH_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  // Every route here reads straight off disk on every request — the data
  // can change from one request to the next (edits, hides, PDF fetches,
  // graph updates). Without this, the browser's HTTP cache can serve a
  // stale response for a plain fetch() to the same URL (e.g. /api/graph),
  // showing outdated fields until a hard reload. Applies to the static
  // HTML too, for the same reason (served fresh from disk every request).
  res.setHeader('Cache-Control', 'no-store');
}

const server = http.createServer(function(req, res) {
  cors(res);

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  if (req.method === 'GET' && req.url === '/') {
    try {
      const html = fs.readFileSync(HTML_FILE, 'utf8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
    } catch(e) {
      res.writeHead(404); res.end('paper-library.html not found');
    }
    return;
  }

  // BibTeX export. ?ids=P001,P004 exports a subset (order preserved);
  // no ids exports the whole non-hidden library. `download=1` sets an
  // attachment filename; otherwise the raw .bib is returned for copying.
  if (req.method === 'GET' && req.url.startsWith('/api/export/bibtex')) {
    try {
      const u = new URL(req.url, 'http://localhost');
      const ids = (u.searchParams.get('ids') || '').trim();
      const r = runBibtexExport(ids);
      if (r.error) { res.writeHead(500); res.end(r.error); return; }
      const headers = { 'Content-Type': 'application/x-bibtex; charset=utf-8' };
      if (u.searchParams.get('download')) {
        headers['Content-Disposition'] = 'attachment; filename="too-many-papers.bib"';
      }
      res.writeHead(200, headers);
      res.end(r.content);
    } catch (e) {
      res.writeHead(500); res.end(String(e.message));
    }
    return;
  }

  if (req.method === 'GET' && req.url === '/api/papers') {
    try {
      const data = loadData();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    } catch(e) {
      res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  if (req.method === 'GET' && req.url === '/api/graph') {
    try {
      const data = loadGraph();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    } catch(e) {
      res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/api/toggle') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const id    = JSON.parse(body).id;
        const found = toggleRead(id);
        if (!found) {
          res.writeHead(404); res.end(JSON.stringify({ error: 'Paper not found' }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/hide') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const id    = JSON.parse(body).id;
        const found = toggleHidden(id);
        if (!found) {
          res.writeHead(404); res.end(JSON.stringify({ error: 'Paper not found' }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/paper-delete') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const id     = JSON.parse(body).id;
        const result = deletePaper(id);
        if (result.error) {
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/node-delete') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const id     = JSON.parse(body).id;
        const result = deleteGraphNode(id);
        if (result.error) {
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadGraph();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/paper-update') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = updatePaperFields(payload.id, payload.patch || {});
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/node-update') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = updateGraphNodeFields(payload.id, payload.patch || {});
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadGraph();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/edge-add') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = addEdge(payload.src, payload.tgt, payload.type, payload.note);
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadGraph();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/edge-delete') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = deleteEdge(payload.src, payload.tgt, payload.type);
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadGraph();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/pdf-note-add') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = addPdfNote(payload.id, payload.page, payload.quote, payload.text);
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/api/pdf-note-delete') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = deletePdfNote(payload.id, payload.noteId);
        if (result.error) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ error: result.error }));
          return;
        }
        const data = loadData();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      } catch(e) {
        res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === 'GET' && req.url.startsWith('/vendor/')) {
    // Vendored client-side libraries (e.g. pdf.js), served as static files —
    // no CDN dependency at runtime, everything ships inside the plugin.
    const name = decodeURIComponent(req.url.slice('/vendor/'.length));
    if (name.includes('..') || name.includes('/') || name.includes('\\')) {
      res.writeHead(400); res.end('Bad request'); return;
    }
    const filepath = path.join(__dirname, 'vendor', name);
    fs.readFile(filepath, function(err, data) {
      if (err) { res.writeHead(404); res.end('Not found'); return; }
      const contentType = name.endsWith('.mjs') ? 'text/javascript; charset=utf-8'
                         : name.endsWith('.js')  ? 'text/javascript; charset=utf-8'
                         : 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': contentType });
      res.end(data);
    });
    return;
  }

  if (req.method === 'GET' && req.url.startsWith('/pdf/')) {
    // The frontend sends the paper's `file` field verbatim (encodeURIComponent'd),
    // e.g. "pdfs/P001.pdf" for automatically-fetched PDFs — resolve it the
    // same way loadData() computes fileExists, so what's reported as
    // present is actually what gets served.
    const file = decodeURIComponent(req.url.slice(5));
    const filepath = resolvePdfPath(file);
    if (!filepath) { res.writeHead(404); res.end('PDF not found'); return; }
    fs.readFile(filepath, function(err, data) {
      if (err) { res.writeHead(404); res.end('PDF not found'); return; }
      res.writeHead(200, { 'Content-Type': 'application/pdf' });
      res.end(data);
    });
    return;
  }

  res.writeHead(404); res.end('Not found');
});

server.listen(PORT, function() {
  console.log('\nToo Many Papers listening on http://localhost:' + PORT);
  console.log('   Data directory: ' + DATA_DIR);
  console.log('   Press Ctrl+C to stop the server.\n');
});

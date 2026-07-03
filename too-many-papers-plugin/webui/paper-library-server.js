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

const PORT        = parseInt(process.env.PORT, 10) || 3737;
// Data lives in the persistent plugin data directory (TOO_MANY_PAPERS_DATA_DIR,
// set to ${CLAUDE_PLUGIN_DATA} when launched via the webui_launch MCP tool), so
// it survives plugin updates and stays in sync with the MCP server (single
// source of truth). Falls back to ../server for manual/dev use.
const DATA_DIR    = process.env.TOO_MANY_PAPERS_DATA_DIR || path.join(__dirname, '..', 'server');
const PAPERS_FILE = path.join(DATA_DIR, '_papers.json');
const VENUES_FILE = path.join(DATA_DIR, '_venues.json');
const GRAPH_FILE  = path.join(DATA_DIR, '_graph.json');
const HTML_FILE   = path.join(__dirname, 'paper-library.html');

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

  return { papers: result, venues: venues, concepts: concepts };
}

function loadGraph() {
  const papersDb = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const graphDb  = JSON.parse(fs.readFileSync(GRAPH_FILE, 'utf8'));

  const papers = papersDb.papers || {};
  const graphNodes = graphDb.nodes || {};
  const graphEdges = graphDb.edges || [];

  const nodes = {};
  for (const [id, node] of Object.entries(graphNodes)) {
    nodes[id] = Object.assign({ id }, node);
  }
  for (const [id, p] of Object.entries(papers)) {
    nodes[id] = {
      id,
      type:     'paper',
      title:    p.title    || '',
      year:     p.year     || '',
      concepts: p.concepts || [],
    };
  }

  const edges = graphEdges.map(e => ({
    from: e.src, to: e.tgt, type: e.type, note: e.note || '',
  }));

  for (const [id, p] of Object.entries(papers)) {
    (p.cites || []).forEach(cid => {
      if (papers[cid]) edges.push({ from: id, to: cid, type: 'cites' });
    });
    (p.concepts || []).forEach(cid => {
      if (nodes[cid]) edges.push({ from: id, to: cid, type: 'concept_tag' });
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

// Fields a paper can be edited through from the UI. Deliberately excludes
// cites/cited_by/cites_unmatched/pdf_source/pdf_status/read/hidden — those
// are system-managed (citation sync, PDF fetch, dedicated toggle buttons)
// and editing them by hand here would fight with that automation instead of
// complementing it.
const PAPER_EDITABLE_FIELDS = new Set([
  'title', 'authors', 'year', 'discovered', 'venue_id', 'venue_detail',
  'source_verified', 'concepts', 'file', 'outside_zone', 'notes', 'url',
]);

// PDF notes (`pdf_notes`) are edited through their own add/delete endpoints
// below rather than through updatePaperFields' generic patch — each note is
// a small structured record (page + text + timestamp), and going through
// dedicated read-modify-write functions avoids a client having to round-trip
// the entire notes array (and risk clobbering a concurrent addition) just to
// append or remove one note.
function addPdfNote(paperId, page, text) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };
  if (!text || !String(text).trim()) return { error: 'Note text cannot be empty.' };

  const note = {
    id: 'N' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    page: page ? parseInt(page, 10) || null : null,
    text: String(text).trim(),
    created: new Date().toISOString(),
  };
  papers[paperId].pdf_notes = papers[paperId].pdf_notes || [];
  papers[paperId].pdf_notes.push(note);
  raw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
  return { ok: true };
}

function deletePdfNote(paperId, noteId) {
  const raw    = JSON.parse(fs.readFileSync(PAPERS_FILE, 'utf8'));
  const papers = raw.papers || {};
  if (!papers[paperId]) return { error: `Paper '${paperId}' not found.` };
  const before = (papers[paperId].pdf_notes || []).length;
  papers[paperId].pdf_notes = (papers[paperId].pdf_notes || []).filter(n => n.id !== noteId);
  if (papers[paperId].pdf_notes.length === before) return { error: `Note '${noteId}' not found.` };
  raw.papers = papers;
  fs.writeFileSync(PAPERS_FILE, JSON.stringify(raw, null, 2), 'utf8');
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
};
const NODE_OPTIONAL_FIELDS = {
  concept:  ['description'],
  project:  ['description'],
  endpoint: ['description'],
  idea:     ['description', 'source'],
  pool:     ['description'],
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

function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
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

  if (req.method === 'POST' && req.url === '/api/pdf-note-add') {
    let body = '';
    req.on('data', function(d) { body += d; });
    req.on('end', function() {
      try {
        const payload = JSON.parse(body);
        const result  = addPdfNote(payload.id, payload.page, payload.text);
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
  console.log('   Sources: _papers.json, _venues.json, _graph.json');
  console.log('   Press Ctrl+C to stop the server.\n');
});

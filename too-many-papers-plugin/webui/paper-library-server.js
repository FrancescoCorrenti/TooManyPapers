/**
 * Paper Library - Mini Server
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
// Data lives in ../server, shared with the MCP server (single source of truth)
const DATA_DIR    = path.join(__dirname, '..', 'server');
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
      fileExists:  p.file ? !!findFileSync(path.join(__dirname, '..'), path.basename(p.file)) : false,
      outsideZone: p.outside_zone     || false,
      notes:       p.notes            || '',
      read:        p.read             || false,
      hidden:      p.hidden           || false,
      cites:       cites,
      citedBy:     citedBy,
    };
  });

  result.sort(function(a, b) {
    if (b.dataScop !== a.dataScop) return b.dataScop.localeCompare(a.dataScop);
    return a.id.localeCompare(b.id);
  });

  return { papers: result, venues: venues, concepts: concepts };
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

  if (req.method === 'GET' && req.url.startsWith('/pdf/')) {
    const filename = path.basename(decodeURIComponent(req.url.slice(5)));
    // Recursively search for the file in __dirname
    function findFile(dir, name, cb) {
      fs.readdir(dir, { withFileTypes: true }, function(err, entries) {
        if (err) return cb(null);
        let i = 0;
        function next() {
          if (i >= entries.length) return cb(null);
          const entry = entries[i++];
          const full = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            findFile(full, name, function(found) {
              if (found) return cb(found);
              next();
            });
          } else if (entry.name === name) {
            cb(full);
          } else {
            next();
          }
        }
        next();
      });
    }
    findFile(__dirname, filename, function(filepath) {
      if (!filepath) { res.writeHead(404); res.end('PDF not found'); return; }
      fs.readFile(filepath, function(err, data) {
        if (err) { res.writeHead(404); res.end('PDF not found'); return; }
        res.writeHead(200, { 'Content-Type': 'application/pdf' });
        res.end(data);
      });
    });
    return;
  }

  res.writeHead(404); res.end('Not found');
});

server.listen(PORT, function() {
  console.log('\nPaper Library listening on http://localhost:' + PORT);
  console.log('   Sources: _papers.json, _venues.json, _graph.json');
  console.log('   Press Ctrl+C to stop the server.\n');
});

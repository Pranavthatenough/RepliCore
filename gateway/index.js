/*
 * gateway/index.js  -  the "front door" of the system.
 *
 * Browsers never talk to the replicas directly. They talk to the gateway, which:
 *   1. Finds out which replica is the leader (asks everyone every second).
 *   2. Forwards every drawing command to the leader, and retries on a new
 *      leader if the old one dies.
 *   3. Pushes committed strokes to all open browser tabs over a WebSocket.
 *   4. Serves the web page (../frontend) and a small API for the dashboard.
 *
 * Run it:  npm install   then   npm start      (needs Node.js 18+)
 */

const http = require('http');
const path = require('path');
const express = require('express');
const { WebSocketServer, WebSocket } = require('ws');

const PORT = Number(process.env.PORT) || 3000;
const REPLICAS = (process.env.REPLICAS || 'http://localhost:5001,http://localhost:5002,http://localhost:5003')
  .split(',').map((url) => url.trim()).filter(Boolean);
const FRONTEND_DIR = process.env.FRONTEND_DIR || path.join(__dirname, '..', 'frontend');

let leaderUrl = null;   // our best guess of who the leader is

function log(...args) {
  console.log(`[${new Date().toTimeString().slice(0, 8)}] [gateway]`, ...args);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Small helper: fetch JSON with a 1 second timeout. Throws on failure.
async function callReplica(url, method = 'GET', body = undefined) {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(method === 'GET' ? 1000 : 3000),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

// ---------------------------------------------------------------------------
// 1. Leader discovery
// ---------------------------------------------------------------------------
async function getClusterStatus() {
  return Promise.all(REPLICAS.map(async (url, i) => {
    try {
      const { data } = await callReplica(`${url}/status`);
      return { index: i, url, up: true, ...data };
    } catch {
      return { index: i, url, up: false };   // container stopped / unreachable
    }
  }));
}

async function findLeader() {
  const nodes = await getClusterStatus();
  // If two nodes both think they lead (rare, during a change), trust the higher term.
  const leaders = nodes
    .filter((n) => n.up && !n.crashed && n.role === 'leader')
    .sort((a, b) => b.term - a.term);
  const newLeader = leaders.length ? leaders[0].url : null;
  if (newLeader !== leaderUrl) {
    log(newLeader ? `leader is now ${newLeader} (term ${leaders[0].term})` : 'no leader right now (election running?)');
    leaderUrl = newLeader;
  }
  return nodes;
}

setInterval(() => findLeader().catch(() => {}), 1000);

// ---------------------------------------------------------------------------
// 2. Forward commands to the leader, retrying through failovers
// ---------------------------------------------------------------------------
async function submitToLeader(command) {
  for (let attempt = 1; attempt <= 6; attempt++) {
    if (!leaderUrl) await findLeader();
    if (leaderUrl) {
      try {
        const { ok, data } = await callReplica(`${leaderUrl}/submit`, 'POST', { command });
        if (ok) return true;
        log(`leader ${leaderUrl} refused: ${data.error}`);
      } catch (err) {
        log(`leader ${leaderUrl} did not answer`);
      }
      leaderUrl = null;            // forget it and look again
    }
    await sleep(400);              // give the cluster time to elect someone
  }
  return false;
}

async function getBoard() {
  // Prefer the leader: it always has the newest committed entries.
  const candidates = leaderUrl ? [leaderUrl, ...REPLICAS] : REPLICAS;
  for (const url of candidates) {
    try {
      const { ok, data } = await callReplica(`${url}/board`);
      if (ok) return data.strokes;
    } catch { /* try the next one */ }
  }
  return [];
}

// ---------------------------------------------------------------------------
// 3. WebSocket: live drawing for every browser tab
// ---------------------------------------------------------------------------
const app = express();
app.use(express.json({ limit: '1mb' }));
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

function sendTo(ws, message) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}
function broadcast(message, except = null) {
  for (const client of wss.clients) if (client !== except) sendTo(client, message);
}

wss.on('connection', async (ws) => {
  log(`browser connected (${wss.clients.size} open)`);
  sendTo(ws, { type: 'board', strokes: await getBoard() });   // full picture first

  ws.on('message', async (raw) => {
    let message;
    try { message = JSON.parse(raw); } catch { return; }

    let command;
    if (message.type === 'stroke' && message.stroke) command = { type: 'stroke', stroke: message.stroke };
    else if (message.type === 'clear') command = { type: 'clear' };
    else return;

    const committed = await submitToLeader(command);
    if (committed) {
      broadcast(message, ws);      // everyone else draws it (the sender already has it)
    } else {
      // Not saved: tell the sender and give them the true board back.
      sendTo(ws, { type: 'error', message: 'Not saved: the cluster has no leader / no majority.' });
      sendTo(ws, { type: 'board', strokes: await getBoard() });
    }
  });

  ws.on('close', () => log(`browser disconnected (${wss.clients.size} open)`));
});

// ---------------------------------------------------------------------------
// 4. HTTP API for the dashboard + the web page itself
// ---------------------------------------------------------------------------
app.get('/api/cluster', async (_req, res) => {
  const nodes = await findLeader();
  res.json({ leader: leaderUrl, nodes });
});

app.post('/api/nodes/:index/:action', async (req, res) => {
  const url = REPLICAS[Number(req.params.index)];
  const action = req.params.action;
  if (!url || !['crash', 'recover'].includes(action)) return res.status(400).json({ error: 'bad request' });
  try {
    await callReplica(`${url}/${action}`, 'POST', {});
    log(`${action} -> ${url}`);
    res.json({ success: true });
  } catch {
    res.status(502).json({ error: 'replica unreachable' });
  }
});

app.use(express.static(FRONTEND_DIR));

server.listen(PORT, () => {
  log(`open http://localhost:${PORT}`);
  log(`replicas: ${REPLICAS.join(', ')}`);
  findLeader().catch(() => {});
});

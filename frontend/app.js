/*
 * frontend/app.js  -  the drawing board and the cluster dashboard.
 *
 * A stroke looks like:  { id, color, size, points: [[x, y], ...] }
 * x and y are between 0 and 1 (fractions of the canvas), so tabs of
 * different sizes still show the same picture.
 */

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const colorInput = document.getElementById('color');
const sizeInput = document.getElementById('size');
const messageEl = document.getElementById('message');
const connectionEl = document.getElementById('connection');

let strokes = [];          // everything on the board
let current = null;        // the stroke being drawn right now
let socket = null;

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------
function resizeCanvas() {
  // Match the canvas pixels to its size on screen, then redraw everything.
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  redraw();
}

function drawStroke(stroke) {
  const { points } = stroke;
  if (!points || points.length === 0) return;
  ctx.strokeStyle = stroke.color;
  ctx.lineWidth = stroke.size * devicePixelRatio;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(points[0][0] * canvas.width, points[0][1] * canvas.height);
  for (const [x, y] of points) ctx.lineTo(x * canvas.width, y * canvas.height);
  if (points.length === 1) ctx.lineTo(points[0][0] * canvas.width + 0.1, points[0][1] * canvas.height);
  ctx.stroke();
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  strokes.forEach(drawStroke);
  if (current) drawStroke(current);
}

function pointFromEvent(event) {
  const rect = canvas.getBoundingClientRect();
  return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
}

canvas.addEventListener('pointerdown', (event) => {
  canvas.setPointerCapture(event.pointerId);
  current = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()),
    color: colorInput.value,
    size: Number(sizeInput.value),
    points: [pointFromEvent(event)],
  };
  redraw();
});

canvas.addEventListener('pointermove', (event) => {
  if (!current) return;
  current.points.push(pointFromEvent(event));
  redraw();
});

canvas.addEventListener('pointerup', () => {
  if (!current) return;
  strokes.push(current);                  // show it straight away...
  send({ type: 'stroke', stroke: current });  // ...and send it to the cluster
  current = null;
});

document.getElementById('clear').addEventListener('click', () => {
  strokes = [];
  redraw();
  send({ type: 'clear' });
});

// ---------------------------------------------------------------------------
// Talking to the gateway (WebSocket)
// ---------------------------------------------------------------------------
function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  } else {
    showMessage('Not connected to the gateway: this change was not saved.');
  }
}

function showMessage(text) {
  messageEl.textContent = text;
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(() => (messageEl.textContent = ''), 4000);
}

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${protocol}://${location.host}/ws`);

  socket.onopen = () => {
    connectionEl.textContent = 'Connected';
    connectionEl.classList.add('ok');
  };

  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'board') {         // the full, committed picture
      strokes = message.strokes;
      redraw();
    } else if (message.type === 'stroke') { // someone else drew something
      strokes.push(message.stroke);
      redraw();
    } else if (message.type === 'clear') {
      strokes = [];
      redraw();
    } else if (message.type === 'error') {
      showMessage(message.message);
    }
  };

  socket.onclose = () => {
    connectionEl.textContent = 'Disconnected, retrying…';
    connectionEl.classList.remove('ok');
    setTimeout(connect, 1500);             // keep trying to reconnect
  };
}

// ---------------------------------------------------------------------------
// Cluster dashboard
// ---------------------------------------------------------------------------
async function refreshCluster() {
  try {
    const response = await fetch('/api/cluster');
    const { nodes } = await response.json();
    document.getElementById('nodes').innerHTML = nodes.map(renderNode).join('');
  } catch {
    document.getElementById('nodes').innerHTML = '<p class="hint">Gateway not reachable.</p>';
  }
}

function renderNode(node) {
  const name = node.node_id || `replica ${node.index + 1}`;
  if (!node.up) {
    return `<div class="node down">
      <div class="node-top"><span class="node-name">${name}</span><span class="role">offline</span></div>
      <div class="node-stats">${node.url} is not answering (container stopped?)</div>
    </div>`;
  }
  const role = node.crashed ? 'crashed' : node.role;
  const css = node.crashed ? 'down' : node.role;
  const button = node.crashed
    ? `<button onclick="nodeAction(${node.index}, 'recover')">Recover</button>`
    : `<button onclick="nodeAction(${node.index}, 'crash')">Crash</button>`;
  return `<div class="node ${css}">
    <div class="node-top"><span class="node-name">${name}</span><span class="role">${role}</span></div>
    <div class="node-stats">term ${node.term} · log ${node.log_length} · committed ${node.commit_index}</div>
    ${button}
  </div>`;
}

async function nodeAction(index, action) {
  await fetch(`/api/nodes/${index}/${action}`, { method: 'POST' });
  refreshCluster();
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
window.addEventListener('resize', resizeCanvas);
resizeCanvas();
connect();
refreshCluster();
setInterval(refreshCluster, 1000);

'use strict';

// ── State ──────────────────────────────────────────────────────────────────

const roomId = location.pathname.split('/').pop();
let socket, myUserId, myColor, myName;
let puzzle = null;
let grid = {};         // "r,c" -> confirmed letter
let pencilGrid = {};   // "r,c" -> tentative letter
let revealedCells = new Set();
let users = {};        // user_id -> { color, name, cursor }
let sel = { row: -1, col: -1, dir: 'across' };
let pencilMode = false;
let showOthers = true;
let verifiedClues = new Set(); // "a-5" / "d-12" — words confirmed correct (shared via server)

// ── Identity persistence ───────────────────────────────────────────────────

function saveIdentity() {
  localStorage.setItem('vw-identity', JSON.stringify({ userId: myUserId, name: myName }));
}

function loadIdentity() {
  try { return JSON.parse(localStorage.getItem('vw-identity') || '{}'); }
  catch { return {}; }
}

// ── WebSocket ──────────────────────────────────────────────────────────────

const MAX_RETRIES = 8;
const RETRY_BASE_MS = 1000;

let _retries = 0;
let _retryTimer = null;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const { userId, name } = loadIdentity();
  const params = new URLSearchParams();
  if (userId) params.set('user_id', userId);
  if (name)   params.set('name', name);
  const qs = params.size ? '?' + params.toString() : '';
  socket = new WebSocket(`${proto}://${location.host}/ws/${roomId}${qs}`);

  socket.onopen = () => {
    setStatus('connected');
    _retries = 0;
    document.getElementById('disconnected-banner').classList.remove('show');
  };

  socket.onclose = () => {
    setStatus('disconnected');
    if (_retries < MAX_RETRIES) {
      // Exponential backoff: 1s, 2s, 4s, 8s … capped at 30s
      const delay = Math.min(RETRY_BASE_MS * 2 ** _retries, 30_000);
      _retries++;
      _retryTimer = setTimeout(connect, delay);
    } else {
      document.getElementById('disconnected-banner').classList.add('show');
    }
  };

  socket.onmessage = e => handleMessage(JSON.parse(e.data));
}

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
}

function setStatus(state) {
  const dot = document.getElementById('status-dot');
  dot.className = state;
  dot.title = state === 'connected' ? 'Connected' : state === 'disconnected' ? 'Disconnected' : 'Connecting…';
}

// ── Message handling ───────────────────────────────────────────────────────

function handleMessage(msg) {
  switch (msg.type) {
    case 'sync': {
      myUserId = msg.user_id;
      myColor  = msg.color;
      myName   = msg.name;
      saveIdentity();
      puzzle   = msg.puzzle;
      grid        = msg.grid || {};
      pencilGrid  = msg.pencil_grid || {};
      revealedCells = new Set(msg.revealed || []);
      verifiedClues = new Set(msg.verified_clues || []);
      msg.users.forEach(u => {
        if (u.user_id !== myUserId)
          users[u.user_id] = { color: u.color, name: u.name, cursor: u.cursor };
      });
      renderPuzzle();
      renderClues();
      applyGrid();
      verifiedClues.forEach(key => _renderVerifiedClue(key));
      msg.users.forEach(u => {
        if (u.user_id !== myUserId && u.cursor)
          showUserSelection(u.user_id, u.color, u.cursor.row, u.cursor.col, u.cursor.direction);
      });
      document.getElementById('puzzle-title').textContent = puzzle.title || 'Untitled';
      const srcLink = document.getElementById('source-link');
      if (puzzle.source_url) {
        srcLink.href = puzzle.source_url;
        srcLink.style.display = '';
      } else {
        srcLink.style.display = 'none';
      }
      updateSolutionsLink(puzzle.solutions_url);
      document.title = puzzle.title ? `${puzzle.title} — VibeWord` : 'VibeWord';
      updatePlayerList();
      updateActionButtons();
      break;
    }

    case 'cell_update': {
      const key = `${msg.row},${msg.col}`;
      if (msg.value) {
        if (msg.pencil) {
          pencilGrid[key] = msg.value; delete grid[key]; revealedCells.delete(key);
        } else {
          grid[key] = msg.value; delete pencilGrid[key];
          if (msg.revealed) revealedCells.add(key); else revealedCells.delete(key);
        }
      } else {
        delete grid[key]; delete pencilGrid[key]; revealedCells.delete(key);
      }
      updateCellDisplay(msg.row, msg.col);
      break;
    }

    case 'cursor_move': {
      if (!users[msg.user_id]) users[msg.user_id] = {};
      Object.assign(users[msg.user_id], {
        color: msg.color, name: msg.name,
        cursor: { row: msg.row, col: msg.col, direction: msg.direction },
      });
      showUserSelection(msg.user_id, msg.color, msg.row, msg.col, msg.direction);
      updateOtherPlayersClues();
      updatePlayerList();
      break;
    }

    case 'user_joined':
      users[msg.user_id] = { color: msg.color, name: msg.name, cursor: null };
      updatePlayerList();
      break;

    case 'user_left':
      clearUserSelection(msg.user_id);
      _clearPointer(msg.user_id);
      delete users[msg.user_id];
      updateOtherPlayersClues();
      updatePlayerList();
      break;

    case 'renamed':
      if (users[msg.user_id]) users[msg.user_id].name = msg.name;
      updatePlayerList();
      break;

    case 'pointer_move':
      if (showOthers) _movePointer(msg.user_id, msg.color, msg.name, msg.x, msg.y);
      break;

    case 'pointer_clear':
      _clearPointer(msg.user_id);
      break;

    case 'clue_verified':
      verifyClue(msg.key);
      break;

    case 'solutions_url':
      updateSolutionsLink(msg.url);
      break;
  }
}

// ── Render ─────────────────────────────────────────────────────────────────

function renderPuzzle() {
  const gridEl = document.getElementById('crossword-grid');
  gridEl.innerHTML = '';
  gridEl.style.gridTemplateColumns = `repeat(${puzzle.width}, 38px)`;

  for (let r = 0; r < puzzle.height; r++) {
    for (let c = 0; c < puzzle.width; c++) {
      const cellData = puzzle.cells[r][c];
      const div = document.createElement('div');
      div.className = 'cell' + (cellData.black ? ' black' : '');
      div.dataset.row = r;
      div.dataset.col = c;

      if (!cellData.black) {
        if (cellData.number) {
          const num = document.createElement('span');
          num.className = 'cell-number';
          num.textContent = cellData.number;
          div.appendChild(num);
        }
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.maxLength = 2;
        inp.dataset.row = r;
        inp.dataset.col = c;
        inp.autocomplete = 'off';
        inp.spellcheck = false;
        inp.autocorrect = 'off';
        inp.autocapitalize = 'characters';
        inp.addEventListener('click', () => handleCellClick(r, c));
        inp.addEventListener('keydown', e => handleKeydown(e, r, c));
        inp.addEventListener('input', e => handleInput(e, r, c));
        div.appendChild(inp);
      }
      gridEl.appendChild(div);
    }
  }
}

function renderClues() {
  renderClueList('across-clues', puzzle.clues.across, 'across');
  renderClueList('down-clues', puzzle.clues.down, 'down');
}

function renderClueList(containerId, clues, dir) {
  const el = document.getElementById(containerId);
  el.innerHTML = '';
  clues.forEach(clue => {
    const li = document.createElement('li');
    li.className = 'clue-item';
    li.id = `clue-${dir[0]}-${clue.number}`;
    li.innerHTML = `<span class="clue-num">${escHtml(clue.label || String(clue.number))}.</span>${escHtml(clue.text)}`;
    li.addEventListener('click', () => jumpToClue(clue.number, dir));
    el.appendChild(li);
  });
}

function applyGrid() {
  for (let r = 0; r < puzzle.height; r++)
    for (let c = 0; c < puzzle.width; c++)
      if (!puzzle.cells[r][c].black) updateCellDisplay(r, c);
  requestAnimationFrame(fitGridToScreen);
}

// ── Grid auto-scaling ──────────────────────────────────────────────────────

function fitGridToScreen() {
  const gridEl = document.getElementById('crossword-grid');
  if (!puzzle || !gridEl) return;

  // Compute the grid's natural pixel dimensions from first principles
  // so we don't need DOM measurement (avoids layout-before-paint timing issues).
  const CELL = 38, GAP = 1, BORDER = 4;
  const naturalW = puzzle.width  * CELL + (puzzle.width  - 1) * GAP + BORDER;
  const naturalH = puzzle.height * CELL + (puzzle.height - 1) * GAP + BORDER;

  const area = document.querySelector('.grid-area');
  if (!area) return;
  const pad = 48; // 1.5rem padding on each side
  const availW = area.clientWidth  - pad;
  const availH = area.clientHeight - pad;

  const scale = Math.min(availW / naturalW, availH / naturalH, 1);
  gridEl.style.zoom = scale < 0.999 ? String(scale) : '';
}

// ── Cell display ───────────────────────────────────────────────────────────

// ── Verified-clue helpers ──────────────────────────────────────────────────

function _parseClueKey(key) {
  const dash = key.indexOf('-');
  return { dir: key[0] === 'a' ? 'across' : 'down', num: parseInt(key.slice(dash + 1)) };
}

function _wordAllCorrect(cells) {
  if (!cells.length) return false;
  return cells.every(([r, c]) => {
    const letter = grid[`${r},${c}`];
    if (!letter) return false;
    const sol = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
    return !!sol && sol !== '#' && letter === sol;
  });
}

function _wordHasRevealedCell(cells) {
  return cells.some(([r, c]) => revealedCells.has(`${r},${c}`));
}

// Re-compute word-correct for one cell based on verifiedClues (used for crossing words).
function updateCellVerifiedDisplay(r, c) {
  const inp = getInput(r, c);
  if (!inp) return;
  let green = false;
  for (const dir of ['across', 'down']) {
    const entry = wordStartEntry(r, c, dir);
    if (entry && verifiedClues.has(`${entry.dir[0]}-${entry.num}`)) { green = true; break; }
  }
  inp.classList.toggle('word-correct', green);
}

// Apply/remove word-correct CSS for a clue key based on current verifiedClues.
function _renderVerifiedClue(key) {
  const { dir, num } = _parseClueKey(key);
  const pos = findClueStart(num);
  if (!pos) return;
  const verified = verifiedClues.has(key);
  chainEntries(getChain(num, dir), dir).forEach(({ num: chainNum, dir: chainDir }) => {
    const clueEl = document.getElementById(`clue-${chainDir[0]}-${chainNum}`);
    if (clueEl) clueEl.classList.toggle('word-correct', verified);
  });
  wordCells(pos.row, pos.col, dir).forEach(([r, c]) => updateCellVerifiedDisplay(r, c));
}

function verifyClue(key) {
  verifiedClues.add(key);
  _renderVerifiedClue(key);
}

function unverifyClue(key) {
  verifiedClues.delete(key);
  _renderVerifiedClue(key);
}

// Withdraws verification for any verified word containing (r,c) that is no longer correct.
// Called on any cell change; never adds green (that's checkWord/checkAll's job).
function recheckWordCorrectness(r, c) {
  if (!puzzle || !puzzle.solution) return;
  for (const dir of ['across', 'down']) {
    const entry = wordStartEntry(r, c, dir);
    if (!entry) continue;
    const wordKey = `${entry.dir[0]}-${entry.num}`;
    if (!verifiedClues.has(wordKey)) continue;
    const cells = wordCells(r, c, dir);
    if (_wordHasRevealedCell(cells) || !_wordAllCorrect(cells)) unverifyClue(wordKey);
  }
}

function updateCellDisplay(r, c) {
  const inp = getInput(r, c);
  if (!inp) return;
  const key = `${r},${c}`;
  const pencilLetter = pencilGrid[key];
  const confirmedLetter = grid[key];
  inp.value = pencilLetter || confirmedLetter || '';
  inp.classList.toggle('pencil', !!pencilLetter);
  inp.classList.toggle('revealed', !pencilLetter && !!confirmedLetter && revealedCells.has(key));
  recheckWordCorrectness(r, c);
}

// ── Per-player selection overlays ──────────────────────────────────────────

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function showUserSelection(userId, color, row, col, dir) {
  clearUserSelection(userId);
  if (!userId || row == null || row < 0) return;
  if (userId !== myUserId && !showOthers) return;

  wordCells(row, col, dir).forEach(([r, c]) => {
    const cellEl = getCell(r, c);
    if (!cellEl) return;
    const isCurrentCell = r === row && c === col;
    const overlay = document.createElement('div');
    overlay.className = 'sel-overlay';
    overlay.dataset.selUser = userId;
    overlay.style.background = hexToRgba(color, isCurrentCell ? 0.55 : 0.22);
    cellEl.insertAdjacentElement('afterbegin', overlay);
  });
}

function clearUserSelection(userId) {
  document.querySelectorAll(`[data-sel-user="${userId}"]`).forEach(el => el.remove());
}

// ── Player list ────────────────────────────────────────────────────────────

function updatePlayerList() {
  if (document.activeElement && document.activeElement.id === 'my-name-el') return;

  const bar = document.getElementById('players-bar');
  bar.innerHTML = '';

  for (const [, user] of Object.entries(users)) {
    const chip = document.createElement('div');
    chip.className = 'player-chip';
    chip.innerHTML =
      `<span class="player-dot" style="background:${user.color}"></span>` +
      `<span class="player-name">${escHtml(user.name || '?')}</span>`;
    bar.appendChild(chip);
  }

  if (myUserId) bar.appendChild(makeMyChip());
}

function makeMyChip() {
  const chip = document.createElement('div');
  chip.className = 'player-chip my-chip';
  if (myColor) {
    chip.style.borderColor = myColor;
    chip.style.background = hexToRgba(myColor, 0.07);
  }

  const dot = document.createElement('span');
  dot.className = 'player-dot';
  dot.style.background = myColor;

  const nameEl = document.createElement('span');
  nameEl.id = 'my-name-el';
  nameEl.contentEditable = 'true';
  nameEl.spellcheck = false;
  nameEl.title = 'Click to rename';
  nameEl.textContent = myName;
  nameEl.addEventListener('blur', handleNameBlur);
  nameEl.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); }
    if (e.key === 'Escape') { nameEl.textContent = myName; nameEl.blur(); }
    e.stopPropagation(); // keep crossword keys from firing
  });

  chip.appendChild(dot);
  chip.appendChild(nameEl);
  return chip;
}

function handleNameBlur() {
  const nameEl = document.getElementById('my-name-el');
  if (!nameEl) return;
  const newName = nameEl.textContent.trim().slice(0, 20);
  if (!newName) { nameEl.textContent = myName; return; }
  if (newName !== myName) {
    myName = newName;
    saveIdentity();
    send({ type: 'rename', name: myName });
  }
}

// ── Selection & navigation ─────────────────────────────────────────────────

function handleCellClick(r, c) {
  if (sel.row === r && sel.col === c) {
    const toggled = flip(sel.dir);
    if (wordLength(r, c, toggled) > 1) { selectCell(r, c, toggled); return; }
  }
  selectCell(r, c, sel.dir);
}

function selectCell(r, c, dir) {
  if (wordLength(r, c, dir) < 2 && wordLength(r, c, flip(dir)) >= 2) dir = flip(dir);

  sel = { row: r, col: c, dir };

  // My own selection overlay (same mechanism as other players)
  if (myUserId && myColor) showUserSelection(myUserId, myColor, r, c, dir);

  document.querySelectorAll('.clue-item.active').forEach(el => {
    el.classList.remove('active');
    el.style.background = '';
    el.style.borderLeftColor = '';
  });

  const inp = getInput(r, c);
  if (inp) { inp.focus(); inp.select(); }

  updateActiveClue(r, c, dir);
  updateOtherPlayersClues();
  updateActionButtons();
  send({ type: 'cursor_move', row: r, col: c, direction: dir });
}

// Single contiguous run of white cells containing (r, c) in direction dir.
function runCells(r, c, dir) {
  const cells = [];
  if (dir === 'across') {
    let sc = c;
    while (sc > 0 && !puzzle.cells[r][sc - 1].black) sc--;
    for (let cc = sc; cc < puzzle.width && !puzzle.cells[r][cc].black; cc++) cells.push([r, cc]);
  } else {
    let sr = r;
    while (sr > 0 && !puzzle.cells[sr - 1][c].black) sr--;
    for (let rr = sr; rr < puzzle.height && !puzzle.cells[rr][c].black; rr++) cells.push([rr, c]);
  }
  return cells;
}

// All cells in the linked-clue chain that contains (r, c) in direction dir,
// ordered as the answer should be filled in.  Falls back to runCells for
// ordinary (non-linked) clues.  Handles cross-direction chains (e.g. a clue
// that continues as an Across segment then back to Down).
function wordCells(r, c, dir) {
  return wordCellsTagged(r, c, dir).map(([wr, wc]) => [wr, wc]);
}

// Like wordCells but each entry is [row, col, segDir] so callers can use the
// correct navigation direction when moving into a different segment.
function wordCellsTagged(r, c, dir) {
  const run = runCells(r, c, dir);
  if (!run.length) return run.map(([wr, wc]) => [wr, wc, dir]);
  const startNum = puzzle.cells[run[0][0]][run[0][1]].number;
  if (!startNum) return run.map(([wr, wc]) => [wr, wc, dir]);
  const chain = getChain(startNum, dir);
  if (chain.length <= 1) return run.map(([wr, wc]) => [wr, wc, dir]);
  const all = [];
  for (const { num, dir: segDir } of chainEntries(chain, dir)) {
    const pos = findClueStart(num);
    if (pos) for (const [wr, wc] of runCells(pos.row, pos.col, segDir)) all.push([wr, wc, segDir]);
  }
  return all;
}

function wordLength(r, c, dir) { return wordCells(r, c, dir).length; }

// Returns the ordered chain for a clue, or a singleton if not linked.
// Each chain entry is either [num, dirKey] (new format, supports cross-direction)
// or a plain number (legacy same-direction format).
function getChain(num, dir) {
  if (!puzzle.links) return [num];
  const dirKey = dir === 'across' ? 'Across' : 'Down';
  return (puzzle.links[dirKey] || {})[num] || [num];
}

// Normalises a chain into [{num, dir}] objects regardless of format.
function chainEntries(chain, fallbackDir) {
  return chain.map(entry =>
    Array.isArray(entry)
      ? { num: entry[0], dir: entry[1] === 'Across' ? 'across' : 'down' }
      : { num: entry, dir: fallbackDir }
  );
}

// Returns {num, dir} of the head of the chain containing (r,c) in direction dir,
// or null if the cell has no clue start.
function wordStartEntry(r, c, dir) {
  const run = runCells(r, c, dir);
  if (!run.length) return null;
  const startNum = puzzle.cells[run[0][0]][run[0][1]].number;
  if (!startNum) return null;
  const chain = getChain(startNum, dir);
  const first = chain[0];
  return Array.isArray(first)
    ? { num: first[0], dir: first[1] === 'Across' ? 'across' : 'down' }
    : { num: first, dir };
}

// Returns the clue number of the first run in the chain (the display number).
function wordStartNumber(r, c, dir) {
  const entry = wordStartEntry(r, c, dir);
  return entry ? entry.num : null;
}

// Returns {row, col} of the cell numbered `num`, or null.
function findClueStart(num) {
  for (let r = 0; r < puzzle.height; r++)
    for (let c = 0; c < puzzle.width; c++)
      if (puzzle.cells[r][c].number === num) return { row: r, col: c };
  return null;
}

function flip(dir) { return dir === 'across' ? 'down' : 'across'; }

// ── Input handling ─────────────────────────────────────────────────────────

function handleKeydown(e, r, c) {
  switch (e.key) {
    case 'ArrowRight':
      e.preventDefault();
      sel.dir === 'across' ? moveBy(r, c, 0, 1) : selectCell(r, c, 'across'); break;
    case 'ArrowLeft':
      e.preventDefault();
      sel.dir === 'across' ? moveBy(r, c, 0, -1) : selectCell(r, c, 'across'); break;
    case 'ArrowDown':
      e.preventDefault();
      sel.dir === 'down' ? moveBy(r, c, 1, 0) : selectCell(r, c, 'down'); break;
    case 'ArrowUp':
      e.preventDefault();
      sel.dir === 'down' ? moveBy(r, c, -1, 0) : selectCell(r, c, 'down'); break;
    case 'Backspace':
      e.preventDefault(); handleBackspace(r, c); break;
    case 'Delete':
      e.preventDefault(); commitCell(r, c, ''); break;
    case 'Tab':
      e.preventDefault(); e.shiftKey ? prevWord() : nextWord(); break;
    default:
      if (e.key.length === 1 && /[a-zA-Z]/.test(e.key)) {
        e.preventDefault();
        commitCell(r, c, e.key.toUpperCase());
        advance(r, c, sel.dir);
      }
  }
}

function handleInput(e, r, c) {
  // Mobile fallback — keydown fired 'Unidentified'
  const val = e.target.value.replace(/[^a-zA-Z]/g, '').toUpperCase().slice(-1);
  e.target.value = val;
  const key = `${r},${c}`;
  const existing = pencilGrid[key] || grid[key] || '';
  if (val === existing) return;
  commitCell(r, c, val);
  if (val) advance(r, c, sel.dir);
}

function commitCell(r, c, letter, { isPencil = pencilMode, isRevealed = false } = {}) {
  const key = `${r},${c}`;
  if (letter) {
    if (isPencil) {
      pencilGrid[key] = letter; delete grid[key]; revealedCells.delete(key);
    } else {
      grid[key] = letter; delete pencilGrid[key];
      if (isRevealed) revealedCells.add(key); else revealedCells.delete(key);
    }
  } else {
    delete grid[key]; delete pencilGrid[key]; revealedCells.delete(key);
  }
  updateCellDisplay(r, c);
  send({ type: 'cell_update', row: r, col: c, value: letter, pencil: isPencil, revealed: isRevealed });
}

function handleBackspace(r, c) {
  const key = `${r},${c}`;
  if (grid[key] || pencilGrid[key]) {
    commitCell(r, c, '');
  } else {
    const cells = wordCellsTagged(r, c, sel.dir);
    const idx = cells.findIndex(([wr, wc]) => wr === r && wc === c);
    if (idx > 0) {
      const [pr, pc, prevDir] = cells[idx - 1];
      commitCell(pr, pc, '');
      selectCell(pr, pc, prevDir);
    }
  }
}

function advance(r, c, dir) {
  const cells = wordCellsTagged(r, c, dir);
  const idx = cells.findIndex(([wr, wc]) => wr === r && wc === c);
  if (idx !== -1 && idx < cells.length - 1) {
    const [nr, nc, nextDir] = cells[idx + 1];
    selectCell(nr, nc, nextDir);
  }
}

function stepForward(r, c, dir) {
  const [dr, dc] = dir === 'down' ? [1, 0] : [0, 1];
  let nr = r + dr, nc = c + dc;
  while (nr >= 0 && nr < puzzle.height && nc >= 0 && nc < puzzle.width) {
    if (!puzzle.cells[nr][nc].black) return [nr, nc];
    nr += dr; nc += dc;
  }
  return [-1, -1];
}

function stepBack(r, c, dir) {
  const [dr, dc] = dir === 'down' ? [-1, 0] : [0, -1];
  let nr = r + dr, nc = c + dc;
  while (nr >= 0 && nr < puzzle.height && nc >= 0 && nc < puzzle.width) {
    if (!puzzle.cells[nr][nc].black) return [nr, nc];
    nr += dr; nc += dc;
  }
  return [-1, -1];
}

function moveBy(r, c, dr, dc) {
  let nr = r + dr, nc = c + dc;
  while (nr >= 0 && nr < puzzle.height && nc >= 0 && nc < puzzle.width) {
    if (!puzzle.cells[nr][nc].black) { selectCell(nr, nc, sel.dir); return; }
    nr += dr; nc += dc;
  }
}

// ── Word navigation (Tab / Shift-Tab) ─────────────────────────────────────

function allWords() {
  const words = [];
  puzzle.clues.across.forEach(cl => {
    // Skip continuation entries — Tab only visits the chain head.
    const first = getChain(cl.number, 'across')[0];
    const headNum = Array.isArray(first) ? first[0] : first;
    if (headNum !== cl.number) return;
    const headDir = Array.isArray(first) ? (first[1] === 'Across' ? 'across' : 'down') : 'across';
    const pos = findClueStart(cl.number);
    if (pos) words.push({ row: pos.row, col: pos.col, dir: headDir });
  });
  puzzle.clues.down.forEach(cl => {
    const first = getChain(cl.number, 'down')[0];
    const headNum = Array.isArray(first) ? first[0] : first;
    if (headNum !== cl.number) return;
    const headDir = Array.isArray(first) ? (first[1] === 'Across' ? 'across' : 'down') : 'down';
    const pos = findClueStart(cl.number);
    if (pos) words.push({ row: pos.row, col: pos.col, dir: headDir });
  });
  return words;
}

function currentWordIndex(words) {
  const wc = wordCells(sel.row, sel.col, sel.dir);
  if (!wc.length) return -1;
  const [sr, sc] = wc[0];
  return words.findIndex(w => w.row === sr && w.col === sc && w.dir === sel.dir);
}

function nextWord() {
  const words = allWords();
  const next = words[(currentWordIndex(words) + 1) % words.length];
  if (next) selectCell(next.row, next.col, next.dir);
}

function prevWord() {
  const words = allWords();
  const i = currentWordIndex(words);
  const prev = words[(i - 1 + words.length) % words.length];
  if (prev) selectCell(prev.row, prev.col, prev.dir);
}

function jumpToClue(number, dir) {
  // Always jump to the head of the chain so selection starts at the first run.
  const first = getChain(number, dir)[0];
  const headNum = Array.isArray(first) ? first[0] : first;
  const headDir = Array.isArray(first) ? (first[1] === 'Across' ? 'across' : 'down') : dir;
  const pos = findClueStart(headNum);
  if (pos) selectCell(pos.row, pos.col, headDir);
}

// ── Clue panel ─────────────────────────────────────────────────────────────

function updateOtherPlayersClues() {
  document.querySelectorAll('[data-other-highlight]').forEach(el => {
    el.style.background = '';
    el.style.borderLeftColor = '';
    el.removeAttribute('data-other-highlight');
  });

  if (!showOthers) return;

  for (const [userId, user] of Object.entries(users)) {
    if (!user.cursor) continue;
    const { row, col, direction } = user.cursor;
    const entry = wordStartEntry(row, col, direction);
    if (!entry) continue;
    const { num, dir: primaryDir } = entry;
    chainEntries(getChain(num, primaryDir), primaryDir).forEach(({ num: chainNum, dir: chainDir }) => {
      const clueEl = document.getElementById(`clue-${chainDir[0]}-${chainNum}`);
      if (!clueEl || clueEl.classList.contains('active')) return;
      clueEl.style.background = hexToRgba(user.color, 0.12);
      clueEl.style.borderLeftColor = hexToRgba(user.color, 0.5);
      clueEl.setAttribute('data-other-highlight', userId);
    });
  }
}

function updateActiveClue(r, c, dir) {
  const entry = wordStartEntry(r, c, dir);
  if (!entry) return;
  const { num, dir: primaryDir } = entry;
  // Highlight every clue item in the chain (primary + continuations).
  chainEntries(getChain(num, primaryDir), primaryDir).forEach(({ num: chainNum, dir: chainDir }) => {
    const clueEl = document.getElementById(`clue-${chainDir[0]}-${chainNum}`);
    if (!clueEl) return;
    clueEl.classList.add('active');
    if (myColor) {
      clueEl.style.background = hexToRgba(myColor, 0.18);
      clueEl.style.borderLeftColor = myColor;
    }
  });
  // Scroll the primary clue into view.
  const primaryEl = document.getElementById(`clue-${primaryDir[0]}-${num}`);
  if (primaryEl) primaryEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  const clueList = primaryDir === 'across' ? puzzle.clues.across : puzzle.clues.down;
  const clue = clueList.find(cl => cl.number === num);
  const dirLabel = primaryDir === 'across' ? 'Across' : 'Down';
  const numLabel = clue ? (clue.label || String(clue.number)) : String(num);
  document.getElementById('clue-display').textContent = clue ? `${numLabel} ${dirLabel}: ${clue.text}` : '';
}

function toggleClueDisplay() {
  const bar = document.getElementById('clue-display');
  const btn = document.getElementById('clue-btn');
  const visible = bar.classList.toggle('visible');
  btn.classList.toggle('active', visible);
  localStorage.setItem('vw-clue-bar', visible ? '1' : '');
}

// ── Reveal ─────────────────────────────────────────────────────────────────

function revealLetter() {
  if (!puzzle.solution || sel.row < 0) return;
  const { row, col } = sel;
  const letter = ((puzzle.solution[row] || [])[col] || '').toUpperCase();
  if (letter && letter !== '#') commitCell(row, col, letter, { isPencil: false, isRevealed: true });
}

function revealWord() {
  if (!puzzle.solution || sel.row < 0) return;
  wordCells(sel.row, sel.col, sel.dir).forEach(([r, c]) => {
    const letter = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
    if (letter && letter !== '#') commitCell(r, c, letter, { isPencil: false, isRevealed: true });
  });
}

// ── Check ──────────────────────────────────────────────────────────────────

function checkWord() {
  if (!puzzle.solution || sel.row < 0) return;
  wordCells(sel.row, sel.col, sel.dir).forEach(([r, c]) => {
    const key = `${r},${c}`;
    const letter = pencilGrid[key] || grid[key];
    if (!letter) return;
    const sol = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
    if (sol && sol !== '#' && letter.toUpperCase() !== sol) commitCell(r, c, '');
  });
  const entry = wordStartEntry(sel.row, sel.col, sel.dir);
  if (!entry) return;
  const wordKey = `${entry.dir[0]}-${entry.num}`;
  const cells = wordCells(sel.row, sel.col, sel.dir);
  if (!_wordHasRevealedCell(cells) && _wordAllCorrect(cells)) {
    verifyClue(wordKey);
    send({ type: 'word_correct', key: wordKey });
  }
}

function checkAll() {
  if (!puzzle.solution) return;
  for (let r = 0; r < puzzle.height; r++) {
    for (let c = 0; c < puzzle.width; c++) {
      if (puzzle.cells[r][c].black) continue;
      const key = `${r},${c}`;
      const letter = pencilGrid[key] || grid[key];
      if (!letter) continue;
      const sol = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
      if (sol && sol !== '#' && letter.toUpperCase() !== sol) commitCell(r, c, '');
    }
  }
  const toVerify = new Set();
  for (let r = 0; r < puzzle.height; r++) {
    for (let c = 0; c < puzzle.width; c++) {
      if (puzzle.cells[r][c].black) continue;
      for (const dir of ['across', 'down']) {
        const entry = wordStartEntry(r, c, dir);
        if (!entry) continue;
        const wordKey = `${entry.dir[0]}-${entry.num}`;
        if (toVerify.has(wordKey) || verifiedClues.has(wordKey)) continue;
        const cells = wordCells(r, c, dir);
        if (!_wordHasRevealedCell(cells) && _wordAllCorrect(cells)) toVerify.add(wordKey);
      }
    }
  }
  toVerify.forEach(key => {
    verifyClue(key);
    send({ type: 'word_correct', key });
  });
}

// ── Clear ──────────────────────────────────────────────────────────────────

// Returns true if every cell in the word that crosses (r,c) in `dir` is filled.
function crossingWordIsComplete(r, c, dir) {
  const cells = wordCells(r, c, dir);
  if (cells.length < 2) return false; // no crossing word here
  return cells.every(([wr, wc]) => !!(grid[`${wr},${wc}`] || pencilGrid[`${wr},${wc}`]));
}

// Clears all cells in the current word that are NOT part of a complete crossing word.
function clearClue() {
  if (sel.row < 0) return;
  const crossDir = flip(sel.dir);
  wordCells(sel.row, sel.col, sel.dir).forEach(([r, c]) => {
    if (!crossingWordIsComplete(r, c, crossDir)) commitCell(r, c, '');
  });
}

// ── Action buttons ─────────────────────────────────────────────────────────

function updateActionButtons() {
  const hasSolution = !!(puzzle && puzzle.solution);
  const hasSelection = sel.row >= 0;
  document.getElementById('reveal-letter-btn').disabled = !hasSolution || !hasSelection;
  document.getElementById('reveal-word-btn').disabled   = !hasSolution || !hasSelection;
  document.getElementById('check-btn').disabled         = !hasSolution || !hasSelection;
  document.getElementById('check-all-btn').disabled     = !hasSolution;
  document.getElementById('clear-btn').disabled         = !hasSelection;
}

// ── Export ─────────────────────────────────────────────────────────────────

function exportIpuz() {
  if (!puzzle) return;

  const puzzleGrid = puzzle.cells.map(row =>
    row.map(cell => {
      if (cell.black) return '#';
      if (cell.number) return { cell: cell.number };
      return 0;
    })
  );

  const savedGrid = puzzle.cells.map((row, r) =>
    row.map((cell, c) => {
      if (cell.black) return '#';
      return grid[`${r},${c}`] || pencilGrid[`${r},${c}`] || 0;
    })
  );

  const ipuz = {
    version: 'http://ipuz.org/v2',
    kind: ['http://ipuz.org/crossword#1'],
    title: puzzle.title || '',
    author: puzzle.author || '',
    block: '#',
    empty: '0',
    dimensions: { width: puzzle.width, height: puzzle.height },
    puzzle: puzzleGrid,
    clues: {
      Across: puzzle.clues.across.map(cl => [cl.number, cl.text]),
      Down:   puzzle.clues.down.map(cl   => [cl.number, cl.text]),
    },
    saved: savedGrid,
  };

  if (puzzle.solution) ipuz.solution = puzzle.solution;

  const blob = new Blob([JSON.stringify(ipuz, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `${(puzzle.title || 'vibeword').replace(/[^a-z0-9]/gi, '_')}.ipuz`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Pencil mode ────────────────────────────────────────────────────────────

function togglePencil() {
  pencilMode = !pencilMode;
  const btn = document.getElementById('pencil-btn');
  btn.classList.toggle('active', pencilMode);
}

function toggleOthers() {
  showOthers = !showOthers;
  const btn = document.getElementById('others-btn');
  btn.classList.toggle('active', showOthers);

  if (!showOthers) {
    Object.keys(users).forEach(clearUserSelection);
    _clearAllPointers();
  } else {
    Object.entries(users).forEach(([uid, u]) => {
      if (u.cursor) showUserSelection(uid, u.color, u.cursor.row, u.cursor.col, u.cursor.direction);
    });
  }
  updateOtherPlayersClues();
}

function togglePencilVisibility() {
  const grid = document.getElementById('crossword-grid');
  const btn  = document.getElementById('show-pencil-btn');
  const hidden = grid.classList.toggle('hide-pencil');
  btn.classList.toggle('active', !hidden);
}

// ── Helpers ────────────────────────────────────────────────────────────────

function getInput(r, c) { return document.querySelector(`input[data-row="${r}"][data-col="${c}"]`); }
function getCell(r, c)  { return document.querySelector(`.cell[data-row="${r}"][data-col="${c}"]`); }
function escHtml(str)   { return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// ── Button wiring ──────────────────────────────────────────────────────────

document.getElementById('clue-btn').addEventListener('click', toggleClueDisplay);
document.getElementById('pencil-btn').addEventListener('click', togglePencil);
document.getElementById('show-pencil-btn').addEventListener('click', togglePencilVisibility);
document.getElementById('others-btn').addEventListener('click', toggleOthers);
document.getElementById('reveal-letter-btn').addEventListener('click', revealLetter);
document.getElementById('reveal-word-btn').addEventListener('click', revealWord);
document.getElementById('check-btn').addEventListener('click', checkWord);
document.getElementById('check-all-btn').addEventListener('click', checkAll);
document.getElementById('clear-btn').addEventListener('click', clearClue);
document.getElementById('export-btn').addEventListener('click', exportIpuz);

document.getElementById('share-btn').addEventListener('click', async () => {
  const btn = document.getElementById('share-btn');
  try {
    await navigator.clipboard.writeText(location.href);
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy Room Link'; }, 2000);
  } catch { prompt('Share this link:', location.href); }
});

document.getElementById('room-id-display').textContent = roomId;

function updateSolutionsLink(url) {
  const el = document.getElementById('solutions-link');
  if (url === null || url === undefined) {
    el.textContent = 'Solutions (15²): searching…';
    el.className = 'room-chip-link muted';
    el.removeAttribute('href');
  } else if (url === '') {
    el.textContent = 'Solutions (15²): not found';
    el.className = 'room-chip-link muted';
    el.removeAttribute('href');
  } else {
    el.textContent = 'Solutions (15²)';
    el.className = 'room-chip-link';
    el.href = url;
  }
}

document.getElementById('room-chip').addEventListener('click', e => {
  if (e.target.closest('.room-chip-panel')) return;
  document.getElementById('room-chip').classList.toggle('open');
});

document.addEventListener('click', e => {
  if (!e.target.closest('#room-chip'))
    document.getElementById('room-chip').classList.remove('open');
});

// ── RMB pointer ────────────────────────────────────────────────────────────

const _pointerEls = {};
let _rmbDown = false;
let _lastPointerSend = 0;
const POINTER_THROTTLE = 40;

function _cursorSvg(color) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="20" viewBox="0 0 16 20" style="display:block">
    <path d="M2 1 L2 15 L5.5 11.5 L8.5 18.5 L10.5 17.5 L7.5 10.5 L12 10.5 Z"
          fill="${color}" stroke="white" stroke-width="1.5"
          stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

function _getOrCreatePointer(userId, color, name) {
  if (_pointerEls[userId]) return _pointerEls[userId];
  const el = document.createElement('div');
  el.style.cssText = 'position:fixed;pointer-events:none;z-index:1000;display:flex;align-items:flex-start;gap:3px';
  el.innerHTML = _cursorSvg(color) +
    `<span style="background:${color};color:white;font-family:inherit;font-size:11px;` +
    `padding:1px 5px;border-radius:3px;white-space:nowrap;margin-top:3px;` +
    `box-shadow:0 1px 3px rgba(0,0,0,0.3)">${escHtml(name)}</span>`;
  document.body.appendChild(el);
  _pointerEls[userId] = el;
  return el;
}

function _movePointer(userId, color, name, x, y) {
  const grid = document.getElementById('crossword-grid');
  if (!grid) return;
  const rect = grid.getBoundingClientRect();
  const el = _getOrCreatePointer(userId, color, name);
  el.style.left = (rect.left + x * rect.width)  + 'px';
  el.style.top  = (rect.top  + y * rect.height) + 'px';
}

function _clearPointer(userId) {
  const el = _pointerEls[userId];
  if (el) { el.remove(); delete _pointerEls[userId]; }
}

function _clearAllPointers() {
  Object.keys(_pointerEls).forEach(_clearPointer);
}

function _sendPointer(e) {
  const grid = document.getElementById('crossword-grid');
  if (!grid) return;
  const rect = grid.getBoundingClientRect();
  send({
    type: 'pointer_move',
    x: (e.clientX - rect.left) / rect.width,
    y: (e.clientY - rect.top)  / rect.height,
  });
  _lastPointerSend = Date.now();
}

// Attach to the grid-area scroll container
document.querySelector('.grid-area').addEventListener('contextmenu', e => e.preventDefault());

document.querySelector('.grid-area').addEventListener('mousedown', e => {
  if (e.button !== 2) return;
  _rmbDown = true;
  if (myColor) {
    const encoded = encodeURIComponent(_cursorSvg(myColor));
    document.body.style.cursor = `url("data:image/svg+xml,${encoded}") 2 1, crosshair`;
    document.body.classList.add('rmb-active');
  }
  _sendPointer(e);
});

document.querySelector('.grid-area').addEventListener('mousemove', e => {
  if (!_rmbDown) return;
  if (Date.now() - _lastPointerSend < POINTER_THROTTLE) return;
  _sendPointer(e);
});

document.addEventListener('mouseup', e => {
  if (e.button !== 2 || !_rmbDown) return;
  _rmbDown = false;
  document.body.style.cursor = '';
  document.body.classList.remove('rmb-active');
  send({ type: 'pointer_clear' });
});

// ── Stream mode toggle ─────────────────────────────────────────────────────

function applyStreamMode(on) {
  const wrap = document.getElementById('clue-sections-wrap');
  const btn  = document.getElementById('clue-mode-btn');
  wrap.classList.toggle('stream-mode', on);
  btn.classList.toggle('active', on);
  if (on) localStorage.setItem('vw-stream', '1');
  else    localStorage.removeItem('vw-stream');
  const active = document.querySelector('.clue-item.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

document.getElementById('clue-mode-btn').addEventListener('click', () => {
  const isStream = !document.getElementById('clue-sections-wrap').classList.contains('stream-mode');
  applyStreamMode(isStream);
});

if (localStorage.getItem('vw-stream')) applyStreamMode(true);

// Clue bar defaults to visible; only hide if user explicitly turned it off
if (localStorage.getItem('vw-clue-bar') === '') {
  document.getElementById('clue-btn').classList.remove('active');
} else {
  document.getElementById('clue-display').classList.add('visible');
}

// ── Clue panel drag-to-resize ──────────────────────────────────────────────

document.getElementById('clue-resize-handle').addEventListener('mousedown', e => {
  e.preventDefault();
  const handle = e.currentTarget;
  const panel  = document.getElementById('clue-panel');
  const startX = e.clientX;
  const startW = panel.offsetWidth;

  handle.classList.add('dragging');
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';

  function onMove(e) {
    const newW = Math.min(Math.max(startW + (startX - e.clientX), 180), 700);
    panel.style.width = newW + 'px';
    fitGridToScreen();
  }

  function onUp() {
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  }

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
});

let _fitTimer;
window.addEventListener('resize', () => {
  clearTimeout(_fitTimer);
  _fitTimer = setTimeout(fitGridToScreen, 150);
});

// ── Boot ───────────────────────────────────────────────────────────────────

connect();

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

// ── Identity persistence ───────────────────────────────────────────────────

function saveIdentity() {
  localStorage.setItem('vw-identity', JSON.stringify({ userId: myUserId, color: myColor, name: myName }));
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
  const { userId, color, name } = loadIdentity();
  const params = new URLSearchParams();
  if (userId) params.set('user_id', userId);
  if (color)  params.set('color', color);
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
  document.getElementById('status-dot').className = state;
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
      msg.users.forEach(u => {
        if (u.user_id !== myUserId)
          users[u.user_id] = { color: u.color, name: u.name, cursor: u.cursor };
      });
      renderPuzzle();
      renderClues();
      applyGrid();
      msg.users.forEach(u => {
        if (u.user_id !== myUserId && u.cursor)
          showUserSelection(u.user_id, u.color, u.cursor.row, u.cursor.col, u.cursor.direction);
      });
      document.getElementById('puzzle-title').textContent = puzzle.title || 'Untitled';
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
      delete users[msg.user_id];
      updateOtherPlayersClues();
      updatePlayerList();
      break;

    case 'renamed':
      if (users[msg.user_id]) users[msg.user_id].name = msg.name;
      updatePlayerList();
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
    li.innerHTML = `<span class="clue-num">${clue.number}.</span>${escHtml(clue.text)}`;
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

function updateCellDisplay(r, c) {
  const inp = getInput(r, c);
  if (!inp) return;
  const key = `${r},${c}`;
  const pencilLetter = pencilGrid[key];
  const confirmedLetter = grid[key];
  inp.value = pencilLetter || confirmedLetter || '';
  inp.classList.toggle('pencil', !!pencilLetter);
  inp.classList.toggle('revealed', !pencilLetter && !!confirmedLetter && revealedCells.has(key));
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

  if (myUserId) bar.appendChild(makeMyChip());

  for (const [, user] of Object.entries(users)) {
    const chip = document.createElement('div');
    chip.className = 'player-chip';
    chip.innerHTML =
      `<span class="player-dot" style="background:${user.color}"></span>` +
      `<span class="player-name">${escHtml(user.name || '?')}</span>`;
    bar.appendChild(chip);
  }
}

function makeMyChip() {
  const chip = document.createElement('div');
  chip.className = 'player-chip my-chip';

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

function wordCells(r, c, dir) {
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

function wordLength(r, c, dir) { return wordCells(r, c, dir).length; }

function wordStartNumber(r, c, dir) {
  const cells = wordCells(r, c, dir);
  return cells.length ? puzzle.cells[cells[0][0]][cells[0][1]].number : null;
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
    const [pr, pc] = stepBack(r, c, sel.dir);
    if (pr !== -1) { commitCell(pr, pc, ''); selectCell(pr, pc, sel.dir); }
  }
}

function advance(r, c, dir) {
  const [nr, nc] = stepForward(r, c, dir);
  if (nr !== -1) selectCell(nr, nc, dir);
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
  const findStart = num => {
    for (let r = 0; r < puzzle.height; r++)
      for (let c = 0; c < puzzle.width; c++)
        if (puzzle.cells[r][c].number === num) return { row: r, col: c };
    return null;
  };
  puzzle.clues.across.forEach(cl => { const p = findStart(cl.number); if (p) words.push({ ...p, dir: 'across' }); });
  puzzle.clues.down.forEach(cl =>   { const p = findStart(cl.number); if (p) words.push({ ...p, dir: 'down' }); });
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
  for (let r = 0; r < puzzle.height; r++)
    for (let c = 0; c < puzzle.width; c++)
      if (puzzle.cells[r][c].number === number) { selectCell(r, c, dir); return; }
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
    const num = wordStartNumber(row, col, direction);
    if (num == null) continue;
    const clueEl = document.getElementById(`clue-${direction[0]}-${num}`);
    if (!clueEl || clueEl.classList.contains('active')) continue;
    clueEl.style.background = hexToRgba(user.color, 0.12);
    clueEl.style.borderLeftColor = hexToRgba(user.color, 0.5);
    clueEl.setAttribute('data-other-highlight', userId);
  }
}

function updateActiveClue(r, c, dir) {
  const num = wordStartNumber(r, c, dir);
  if (num == null) return;
  const clueEl = document.getElementById(`clue-${dir[0]}-${num}`);
  if (clueEl) {
    clueEl.classList.add('active');
    if (myColor) {
      clueEl.style.background = hexToRgba(myColor, 0.18);
      clueEl.style.borderLeftColor = myColor;
    }
    clueEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  const clueList = dir === 'across' ? puzzle.clues.across : puzzle.clues.down;
  const clue = clueList.find(cl => cl.number === num);
  const label = dir === 'across' ? 'Across' : 'Down';
  document.getElementById('active-clue').textContent = clue ? `${num} ${label}: ${clue.text}` : '';
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
    const correct = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
    if (correct && correct !== '#' && letter.toUpperCase() !== correct) commitCell(r, c, '');
  });
}

function checkAll() {
  if (!puzzle.solution) return;
  for (let r = 0; r < puzzle.height; r++) {
    for (let c = 0; c < puzzle.width; c++) {
      if (puzzle.cells[r][c].black) continue;
      const key = `${r},${c}`;
      const letter = pencilGrid[key] || grid[key];
      if (!letter) continue;
      const correct = ((puzzle.solution[r] || [])[c] || '').toUpperCase();
      if (correct && correct !== '#' && letter.toUpperCase() !== correct) commitCell(r, c, '');
    }
  }
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
  btn.textContent = pencilMode ? '✏ Pencil' : 'Pen';
}

function toggleOthers() {
  showOthers = !showOthers;
  const btn = document.getElementById('others-btn');
  btn.classList.toggle('active', showOthers);
  btn.textContent = showOthers ? 'Others: On' : 'Others: Off';

  if (!showOthers) {
    Object.keys(users).forEach(clearUserSelection);
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
  btn.textContent = hidden ? 'Marks: Off' : 'Marks: On';
}

// ── Helpers ────────────────────────────────────────────────────────────────

function getInput(r, c) { return document.querySelector(`input[data-row="${r}"][data-col="${c}"]`); }
function getCell(r, c)  { return document.querySelector(`.cell[data-row="${r}"][data-col="${c}"]`); }
function escHtml(str)   { return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// ── Button wiring ──────────────────────────────────────────────────────────

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
    setTimeout(() => { btn.textContent = 'Copy link'; }, 2000);
  } catch { prompt('Share this link:', location.href); }
});

// ── Stream mode toggle ─────────────────────────────────────────────────────

function applyStreamMode(on) {
  const wrap = document.getElementById('clue-sections-wrap');
  const btn  = document.getElementById('clue-mode-btn');
  wrap.classList.toggle('stream-mode', on);
  btn.textContent = on ? 'Clues: Columns' : 'Clues: Rows';
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

// MidKnight page: draws the state the server sends and posts moves.
// It never applies the rules itself: legal moves come from the server.
"use strict";

const FILES = "abcde";
const PIECE_NAMES = { K: "king", N: "knight", B: "bishop", R: "rook", Q: "queen", P: "pawn" };
const REASONS = {
  checkmate: "by checkmate",
  stalemate: "by stalemate",
  "no progress": "21 plies without a pawn move or capture",
  "insufficient material": "by insufficient material",
};
const DRAG_THRESHOLD = 4; // px before a press becomes a drag

const $ = (sel) => document.querySelector(sel);
const boardEl = $("#board");
const promoEl = $("#promo");

let state = null;     // last state from the server
let options = null;   // /api/options
let selected = null;  // square name of the selected piece
let flipped = loadPref("flipped") === "1";
let soundOn = loadPref("sound") !== "0";
let busy = false;
let drag = null;      // pointer interaction in progress
let botThinking = false;
let lastRequest = null; // body of the last POST /api/games (for Rematch)

// ---- small helpers ---------------------------------------------------------

function loadPref(key) {
  try { return localStorage.getItem("midknight." + key); } catch { return null; }
}
function savePref(key, value) {
  try { localStorage.setItem("midknight." + key, value); } catch { /* storage unavailable: fine */ }
}

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch("/api" + path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    err.status = res.status;
    throw err;
  }
  return data;
}

function showError(msg) {
  const el = $("#error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

const sqName = (file, rank) => FILES[file] + (rank + 1);
const sqIndex = (name) => (Number(name[1]) - 1) * 5 + FILES.indexOf(name[0]);
const sqEl = (name) => boardEl.querySelector(`[data-sq="${name}"]`);
const pieceAt = (name) => state.board[sqIndex(name)];

function pieceImg(code) {
  const img = document.createElement("img");
  img.src = `pieces/${code}.svg`;
  img.alt = `${code[0] === "w" ? "White" : "Black"} ${PIECE_NAMES[code[1]]}`;
  img.draggable = false;
  return img;
}

// Legal moves grouped by origin square: { "c2": [{to:"c3", promo:""}, ...] }
function legalByFrom() {
  const map = {};
  for (const u of state ? state.legal : []) {
    (map[u.slice(0, 2)] ||= []).push({ to: u.slice(2, 4), promo: u.slice(4) });
  }
  return map;
}
const movesFrom = (from) => (legalByFrom()[from] || []);
const choicesFor = (from, to) => movesFrom(from).filter((m) => m.to === to);

// ---- players ------------------------------------------------------------------

const isBotGame = () => !!state && (state.players.w.type === "bot" || state.players.b.type === "bot");
const humanColor = () => (isBotGame() ? (state.players.w.type === "human" ? "w" : "b") : null);
const botPlayer = () => (isBotGame() ? state.players[humanColor() === "w" ? "b" : "w"] : null);

// "Classical engine · Level 4 (Club)"
function botTitle(p) {
  const meta = options && options.bots.find((b) => b.name === p.name);
  if (!meta) return p.label;
  const parts = meta.options.map((o) => {
    const v = p.settings[o.key];
    if (o.type === "int") return `${o.label} ${v}` + (o.labels[String(v)] ? ` (${o.labels[String(v)]})` : "");
    const c = o.choices.find((x) => String(x.value) === String(v));
    return `${o.label}: ${c ? c.label : v}`;
  });
  return [meta.label, ...parts].join(" · ");
}

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- sound ------------------------------------------------------------------

const Sound = (() => {
  const names = ["move", "capture", "check", "mate", "draw", "start"];
  const AC = window.AudioContext || window.webkitAudioContext;
  let ctx = null;
  const buffers = {};
  const fallback = {};

  function unlock() {
    if (!AC) return;
    if (!ctx) {
      ctx = new AC();
      for (const n of names) {
        fetch(`sounds/${n}.mp3`)
          .then((r) => r.arrayBuffer())
          .then((b) => ctx.decodeAudioData(b))
          .then((buf) => { buffers[n] = buf; })
          .catch(() => {});
      }
    }
    if (ctx.state === "suspended") ctx.resume();
  }

  function play(name) {
    if (!soundOn) return;
    if (ctx && buffers[name]) {
      const src = ctx.createBufferSource();
      src.buffer = buffers[name];
      src.connect(ctx.destination);
      src.start();
      return;
    }
    // Not decoded yet (or no Web Audio): plain <audio>.
    const a = (fallback[name] ||= new Audio(`sounds/${name}.mp3`));
    try { a.currentTime = 0; a.play().catch(() => {}); } catch { /* ignore */ }
  }

  return { unlock, play };
})();

function soundFor(s) {
  if (s.outcome) return s.outcome.reason === "checkmate" ? "mate" : "draw";
  if (s.check) return "check";
  const last = s.moves[s.moves.length - 1] || "";
  return last.includes("x") ? "capture" : "move";
}

// ---- drawing ----------------------------------------------------------------

// Before a game exists: starting position, nothing to click, only "Start a game".
function renderIntro() {
  $("#intro").hidden = false;
  $("#game-ui").hidden = true;
  $("#rules").hidden = true;
  boardEl.classList.add("idle");
  drawBoard((options && options.start_board) || Array(30).fill(null), {}, new Set());
}

function render() {
  if (!state) return renderIntro();
  $("#intro").hidden = true;
  $("#game-ui").hidden = false;
  $("#rules").hidden = false;
  boardEl.classList.remove("idle");
  const legal = legalByFrom();
  const targets = new Set(selected ? (legal[selected] || []).map((t) => t.to) : []);
  drawBoard(state.board, legal, targets);
  renderStatus();
  renderMoves();
  $("#btn-undo").disabled = busy || !state.can_undo;
}

function drawBoard(board, legal, targets) {
  boardEl.replaceChildren();
  for (let row = 0; row < 6; row++) {
    for (let col = 0; col < 5; col++) {
      const rank = flipped ? row : 5 - row;
      const file = flipped ? 4 - col : col;
      const name = sqName(file, rank);
      const piece = board[rank * 5 + file];
      const el = document.createElement("div");
      el.className = "sq " + ((file + rank) % 2 === 0 ? "light" : "dark");
      el.dataset.sq = name;
      if (state) el.tabIndex = 0;
      el.setAttribute("role", "gridcell");
      el.setAttribute("aria-label", name + (piece ? " " + pieceImg(piece).alt : ""));
      const lm = state && state.last_move;
      if (lm && (lm.from === name || lm.to === name)) el.classList.add("last");
      if (name === selected) el.classList.add("selected");
      if (state && state.check === name) el.classList.add("check");
      if (piece && legal[name]) el.classList.add("movable");
      if (piece) el.append(pieceImg(piece));
      if (targets.has(name)) {
        const dot = document.createElement("span");
        dot.className = "target" + (piece ? " capture" : "");
        el.append(dot);
      }
      if (row === 5) el.append(coord("file", FILES[file]));
      if (col === 0) el.append(coord("rank", String(rank + 1)));
      boardEl.append(el);
    }
  }
}

function coord(kind, text) {
  const s = document.createElement("span");
  s.className = "coord " + kind;
  s.textContent = text;
  return s;
}

function outcomeText(outcome) {
  const detail = REASONS[outcome.reason] || "";
  if (outcome.result === "1/2-1/2") return { title: "Draw!", detail };
  const winner = outcome.result === "1-0" ? "w" : "b";
  if (isBotGame()) {
    return winner === humanColor() ? { title: "You win!", detail } : { title: `${botPlayer().label} wins!`, detail };
  }
  return { title: `${winner === "w" ? "White" : "Black"} wins!`, detail };
}

function renderStatus() {
  const el = $("#status");
  el.replaceChildren();
  el.classList.toggle("over", !!state.outcome);
  const sw = document.createElement("span");
  let text;
  if (state.outcome) {
    const { title, detail } = outcomeText(state.outcome);
    text = `${title.replace("!", "")} ${detail}`.replace("Draw 21", "Draw: 21");
    const r = state.outcome.result;
    sw.className = "swatch " + (r === "1-0" ? "w" : r === "0-1" ? "b" : "");
    if (r === "1/2-1/2") sw.style.background = "linear-gradient(90deg, #fff 50%, #111 50%)";
  } else if (isBotGame()) {
    text = state.bot_to_move ? `${botPlayer().label} is thinking` : "Your move" + (state.check ? " — check!" : "");
    sw.className = "swatch " + state.turn;
  } else {
    const side = state.turn === "w" ? "White" : "Black";
    text = `${side} to move` + (state.check ? " — check!" : "");
    sw.className = "swatch " + state.turn;
  }
  el.append(sw, document.createTextNode(text));
  if (!state.outcome && state.bot_to_move) {
    const dots = document.createElement("span");
    dots.className = "thinking";
    dots.innerHTML = "<i></i><i></i><i></i>";
    el.append(dots);
  }
  const matchup = $("#matchup");
  matchup.hidden = !isBotGame();
  if (isBotGame()) {
    matchup.textContent = `You (${humanColor() === "w" ? "White" : "Black"}) vs ${botTitle(botPlayer())}`;
  }

  const r = state.rules;
  const promo = r.promotions.split("").map((c) => PIECE_NAMES[c]).join(", ");
  $("#rules").textContent = `Seed ${state.seed} · ` +
    `Promotion: ${promo} · Pawns: ${r.double_push ? "two-square first move, en passant" : "one square only"}` +
    (state.outcome ? "" : ` · No-progress count ${state.clock}/21`);
}

function renderMoves() {
  const list = $("#moves");
  list.replaceChildren();
  const moves = state.moves;
  if (!moves.length) {
    const li = document.createElement("li");
    li.innerHTML = '<span class="empty">No moves yet.</span>';
    list.append(li);
  }
  for (let i = 0; i < moves.length; i += 2) {
    const li = document.createElement("li");
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = i / 2 + 1 + ".";
    li.append(num);
    for (const j of [i, i + 1]) {
      const span = document.createElement("span");
      span.className = "mv" + (j === moves.length - 1 ? " current" : "");
      span.textContent = moves[j] || "";
      li.append(span);
    }
    list.append(li);
  }
  if (state.outcome) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="result-line"></span>`;
    li.firstChild.textContent = state.outcome.result.replace("1/2", "½");
    list.append(li);
  }
  list.scrollTop = list.scrollHeight;
}

// ---- making moves -------------------------------------------------------------

// Slide the piece image from one square to another (click moves).
function slide(from, to) {
  const img = sqEl(from)?.querySelector("img");
  const dest = sqEl(to);
  if (!img || !dest) return Promise.resolve();
  const a = img.getBoundingClientRect();
  const b = dest.getBoundingClientRect();
  const dx = b.left + b.width / 2 - (a.left + a.width / 2);
  const dy = b.top + b.height / 2 - (a.top + a.height / 2);
  img.classList.add("sliding");
  // eslint-disable-next-line no-unused-expressions
  img.offsetWidth; // start the transition from the current position
  img.style.transform = `translate(${dx}px, ${dy}px)`;
  return new Promise((resolve) => {
    img.addEventListener("transitionend", resolve, { once: true });
    setTimeout(resolve, 200);
  });
}

// Put the dragged piece on its target square right away (drop moves).
function placeAt(from, to) {
  const img = sqEl(from)?.querySelector("img");
  const dest = sqEl(to);
  if (!img || !dest) return;
  img.classList.remove("dragging");
  dest.querySelector("img")?.remove();
  dest.prepend(img);
}

async function play(uci, how) {
  const from = uci.slice(0, 2), to = uci.slice(2, 4);
  busy = true;
  selected = null;
  boardEl.querySelectorAll(".target").forEach((t) => t.remove());
  boardEl.querySelectorAll(".selected").forEach((t) => t.classList.remove("selected"));
  const animation = how === "drop" ? (placeAt(from, to), null) : slide(from, to);
  const before = state;
  try {
    const [next] = await Promise.all([api(`/games/${state.id}/move`, { move: uci }), animation]);
    state = next;
    showError("");
    Sound.play(soundFor(state));
  } catch (e) {
    showError(e.message);
  } finally {
    busy = false;
    render();
  }
  if (!before.outcome && state.outcome) setTimeout(showGameOver, 450);
  else if (state.bot_to_move) botMove();
}

// Ask the server for the computer's move, then slide it onto the board.
async function botMove() {
  if (!state || !state.bot_to_move || botThinking) return;
  botThinking = true;
  busy = true;
  render();
  const id = state.id;
  const before = state;
  try {
    const [next] = await Promise.all([api(`/games/${id}/bot`, {}), delay(350)]);
    if (!state || state.id !== id) return; // a new game was started meanwhile
    await slide(next.last_move.from, next.last_move.to);
    state = next;
    Sound.play(soundFor(state));
    showError("");
  } catch (e) {
    if (e.status !== 409) showError(e.message);
  } finally {
    botThinking = false;
    busy = false;
    render();
  }
  if (state && state.id === id && !before.outcome && state.outcome) setTimeout(showGameOver, 450);
}

function tryMove(from, to, how) {
  const choices = choicesFor(from, to);
  if (choices.length === 1) { play(from + to, how); return true; }
  if (choices.length > 1) { askPromotion(from, to, choices.map((c) => c.promo)); return true; }
  return false;
}

function askPromotion(from, to, promos) {
  const color = state.turn;
  const order = ["q", "r", "b", "n"].filter((p) => promos.includes(p));
  promoEl.replaceChildren();
  for (const p of order) {
    const b = document.createElement("button");
    b.type = "button";
    b.title = "Promote to " + PIECE_NAMES[p.toUpperCase()];
    b.append(pieceImg(color + p.toUpperCase()));
    b.addEventListener("click", () => { hidePromotion(); play(from + to + p, "drop"); });
    promoEl.append(b);
  }
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "cancel";
  cancel.textContent = "✕";
  cancel.title = "Cancel";
  cancel.addEventListener("click", () => { hidePromotion(); selected = null; render(); });
  promoEl.append(cancel);

  // Place the picker over the target square's column, growing toward the centre.
  const el = sqEl(to);
  const size = el.offsetWidth;
  promoEl.style.width = size + "px";
  promoEl.style.left = el.offsetLeft + "px";
  const atTop = el.offsetTop < boardEl.offsetHeight / 2;
  promoEl.style.flexDirection = atTop ? "column" : "column-reverse";
  promoEl.style.top = atTop ? el.offsetTop + "px" : "";
  promoEl.style.bottom = atTop ? "" : boardEl.offsetHeight - el.offsetTop - size + "px";
  promoEl.hidden = false;
  promoEl.querySelector("button").focus();
}

function hidePromotion() {
  promoEl.hidden = true;
}

// ---- pointer: click-to-move and drag-and-drop ------------------------------------

function squareAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const sq = el && el.closest ? el.closest(".sq") : null;
  return sq && boardEl.contains(sq) ? sq.dataset.sq : null;
}

function onPointerDown(e) {
  if (e.button !== 0 || !state || busy || !promoEl.hidden || drag) return;
  const sq = e.target.closest(".sq");
  if (!sq) return;
  Sound.unlock();
  const name = sq.dataset.sq;

  // A click on a legal target of the selected piece moves it.
  if (selected && selected !== name && tryMove(selected, name, "slide")) return;

  if (movesFrom(name).length) {
    const wasSelected = selected === name;
    selected = name;
    render();
    const img = sqEl(name).querySelector("img");
    drag = { from: name, id: e.pointerId, x0: e.clientX, y0: e.clientY, img, ghost: null, over: null, wasSelected };
    e.preventDefault();
    return;
  }
  if (selected) { selected = null; render(); }
}

function onPointerMove(e) {
  if (!drag || e.pointerId !== drag.id) return;
  if (!drag.ghost) {
    if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < DRAG_THRESHOLD) return;
    const size = drag.img.getBoundingClientRect().width;
    drag.ghost = drag.img.cloneNode();
    drag.ghost.className = "ghost";
    drag.ghost.style.width = drag.ghost.style.height = size + "px";
    drag.size = size;
    document.body.append(drag.ghost);
    drag.img.classList.add("dragging");
    document.body.classList.add("grabbing");
  }
  const half = drag.size / 2;
  drag.ghost.style.transform = `translate(${e.clientX - half}px, ${e.clientY - half}px)`;
  const over = squareAt(e.clientX, e.clientY);
  if (over !== drag.over) {
    if (drag.over) sqEl(drag.over)?.classList.remove("hover");
    drag.over = over;
    if (over && over !== drag.from && choicesFor(drag.from, over).length) sqEl(over).classList.add("hover");
  }
}

function endDrag() {
  if (!drag) return;
  drag.ghost?.remove();
  if (drag.over) sqEl(drag.over)?.classList.remove("hover");
  document.body.classList.remove("grabbing");
  drag = null;
}

function onPointerUp(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const d = drag;
  endDrag();
  if (!d.ghost) {
    // A plain click on a piece: a second click on the same piece deselects it.
    if (d.wasSelected) { selected = null; render(); }
    return;
  }
  const to = squareAt(e.clientX, e.clientY);
  if (to && to !== d.from && tryMove(d.from, to, "drop")) return;
  render(); // dropped off the board or on an illegal square: snap back, keep it selected
}

function onPointerCancel() {
  if (!drag) return;
  endDrag();
  render();
}

// Keyboard: Enter/Space on a focused square works like a click.
function onSquareKey(name) {
  if (!state || busy || !promoEl.hidden) return;
  Sound.unlock();
  if (selected && selected !== name && tryMove(selected, name, "slide")) return;
  selected = movesFrom(name).length && name !== selected ? name : null;
  render();
  sqEl(name)?.focus();
}

// ---- buttons ------------------------------------------------------------------

async function undo() {
  if (!state || busy || !state.can_undo) return;
  hidePromotion();
  closeGameOver();
  busy = true;
  selected = null;
  try {
    state = await api(`/games/${state.id}/undo`, {});
    showError("");
    Sound.play("move");
  } catch (e) {
    showError(e.message);
  } finally {
    busy = false;
    render();
  }
}

function flip() {
  if (!state) return;
  flipped = !flipped;
  savePref("flipped", flipped ? "1" : "0");
  hidePromotion();
  render();
}

function renderSoundButton() {
  const b = $("#btn-sound");
  b.title = (soundOn ? "Sound on" : "Sound off") + " (M to toggle)";
  b.setAttribute("aria-pressed", String(soundOn));
}

function toggleSound() {
  soundOn = !soundOn;
  savePref("sound", soundOn ? "1" : "0");
  renderSoundButton();
  if (soundOn) { Sound.unlock(); Sound.play("move"); }
}

// ---- game over pop-up -------------------------------------------------------------

function showGameOver() {
  if (!state || !state.outcome) return;
  const dlg = $("#over-dialog");
  const { title, detail } = outcomeText(state.outcome);
  const r = state.outcome.result;
  $("#over-title").textContent = title;
  $("#over-detail").textContent = detail.charAt(0).toUpperCase() + detail.slice(1);
  const img = $("#over-piece");
  img.src = `pieces/${r === "0-1" ? "b" : "w"}${r === "1/2-1/2" ? "N" : "K"}.svg`;
  confetti(r === "1/2-1/2" ? 0 : 70);
  dlg.showModal();
  $("#over-rematch").focus();
}

function closeGameOver() {
  const dlg = $("#over-dialog");
  if (dlg.open) dlg.close();
}

function confetti(n) {
  const box = $("#over-dialog .confetti");
  box.replaceChildren();
  const colors = ["#a51c30", "#f3ecd0", "#ffc83d", "#ffffff", "#1f1b1b"];
  for (let i = 0; i < n; i++) {
    const p = document.createElement("i");
    p.style.left = Math.random() * 100 + "vw";
    p.style.background = colors[i % colors.length];
    p.style.setProperty("--dx", (Math.random() - 0.5) * 30 + "vw");
    p.style.setProperty("--spin", (Math.random() * 4 - 2) * 360 + "deg");
    p.style.animationDuration = 1.8 + Math.random() * 1.6 + "s";
    p.style.animationDelay = Math.random() * 0.4 + "s";
    box.append(p);
  }
}

// ---- new game dialog -------------------------------------------------------------

function savedRequest() {
  try { return JSON.parse(loadPref("newgame")) || {}; } catch { return {}; }
}

function buildDialog() {
  const r = options.rules;
  const saved = savedRequest();
  const rules = (state && state.rules) || saved.rules || r.default;
  fillChoices($("#opt-promotions"), "promotions", r.promotions, rules.promotions);
  fillChoices($("#opt-double"), "double_push", r.double_push, rules.double_push);
  const form = $("#new-form");
  form.elements.mode.value = saved.mode || "bot";
  form.elements.color.value = saved.color || "w";
  form.elements.seed.value = "";
  const bots = options.bots.map((b) => ({ value: b.name, label: b.label, description: b.description }));
  const botName = saved.bot && options.bots.some((b) => b.name === saved.bot.name) ? saved.bot.name
    : (options.bots.find((b) => b.name === "classical") || options.bots[0]).name;
  fillChoices($("#opt-bot"), "bot", bots, botName);
  fillBotSettings(botName, saved.bot && saved.bot.name === botName ? saved.bot.settings : {});
  syncDialog();
}

// Settings declared by the chosen bot: an int range becomes a slider, a fixed list a menu.
function fillBotSettings(name, values) {
  const box = $("#opt-bot-settings");
  box.replaceChildren();
  const meta = options.bots.find((b) => b.name === name);
  for (const o of meta ? meta.options : []) {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.textContent = o.label;
    label.htmlFor = "set-" + o.key;
    wrap.append(label);
    const value = values && values[o.key] !== undefined ? values[o.key] : o.default;
    if (o.type === "int") {
      const row = document.createElement("div");
      row.className = "range-row";
      const input = document.createElement("input");
      Object.assign(input, { type: "range", id: "set-" + o.key, min: o.low, max: o.high, step: 1, value });
      input.dataset.key = o.key;
      const out = document.createElement("output");
      const show = () => { out.textContent = input.value + (o.labels[input.value] ? ` · ${o.labels[input.value]}` : ""); };
      input.addEventListener("input", show);
      show();
      row.append(input, out);
      wrap.append(row);
    } else {
      const select = document.createElement("select");
      select.id = "set-" + o.key;
      select.dataset.key = o.key;
      for (const c of o.choices) {
        const opt = document.createElement("option");
        opt.value = String(c.value);
        opt.textContent = c.label;
        opt.selected = String(c.value) === String(value);
        select.append(opt);
      }
      wrap.append(select);
    }
    if (o.description) {
      const small = document.createElement("small");
      small.textContent = o.description;
      wrap.append(small);
    }
    box.append(wrap);
  }
}

function syncDialog() {
  $("#bot-section").hidden = $("#new-form").elements.mode.value !== "bot";
}

function readDialog() {
  const form = $("#new-form");
  const request = {
    rules: {
      promotions: form.elements.promotions.value,
      double_push: form.elements.double_push.value === "true",
    },
    mode: form.elements.mode.value,
  };
  if (request.mode === "bot") {
    const settings = {};
    $("#opt-bot-settings").querySelectorAll("[data-key]").forEach((el) => {
      settings[el.dataset.key] = el.type === "range" ? Number(el.value) : el.value;
    });
    request.bot = { name: form.elements.bot.value, settings };
    request.color = form.elements.color.value;
  }
  const seed = form.elements.seed.value.trim();
  if (seed) request.seed = Number(seed);
  return request;
}

function fillChoices(fieldset, name, choices, value) {
  fieldset.querySelectorAll("label").forEach((l) => l.remove());
  for (const c of choices) {
    const label = document.createElement("label");
    label.className = "choice";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = name;
    input.value = String(c.value);
    input.checked = c.value === value;
    const text = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = c.label;
    const small = document.createElement("small");
    small.textContent = c.description;
    text.append(strong, small);
    label.append(input, text);
    fieldset.append(label);
  }
}

async function newGame(request, quiet = false) {
  hidePromotion();
  closeGameOver();
  selected = null;
  state = await api("/games", request);
  lastRequest = { ...request };
  delete lastRequest.seed; // a rematch gets a fresh seed
  savePref("game", state.id);
  savePref("newgame", JSON.stringify(lastRequest));
  if (isBotGame()) {
    flipped = humanColor() === "b"; // your pieces at the bottom
    savePref("flipped", flipped ? "1" : "0");
  }
  showError("");
  render();
  if (!quiet) Sound.play("start");
  if (state.bot_to_move) botMove();
}

function openNewDialog() {
  closeGameOver();
  buildDialog();
  $("#new-dialog").showModal();
}

$("#new-dialog").addEventListener("close", async () => {
  if ($("#new-dialog").returnValue !== "start") return;
  try { await newGame(readDialog()); } catch (e) { showError(e.message); }
});
$("#new-form").addEventListener("change", (e) => {
  if (e.target.name === "mode") syncDialog();
  if (e.target.name === "bot") fillBotSettings(e.target.value, {});
});

// ---- wiring -------------------------------------------------------------------

boardEl.addEventListener("pointerdown", onPointerDown);
document.addEventListener("pointermove", onPointerMove);
document.addEventListener("pointerup", onPointerUp);
document.addEventListener("pointercancel", onPointerCancel);
boardEl.addEventListener("contextmenu", (e) => e.preventDefault());
boardEl.addEventListener("keydown", (e) => {
  const sq = e.target.closest(".sq");
  if (sq && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSquareKey(sq.dataset.sq); }
});
$("#btn-new").addEventListener("click", openNewDialog);
$("#btn-start").addEventListener("click", () => { Sound.unlock(); openNewDialog(); });
$("#btn-undo").addEventListener("click", undo);
$("#btn-flip").addEventListener("click", flip);
$("#btn-sound").addEventListener("click", toggleSound);
$("#over-close").addEventListener("click", closeGameOver);
$("#over-new").addEventListener("click", openNewDialog);
$("#over-rematch").addEventListener("click", () => {
  Sound.unlock();
  newGame(lastRequest || savedRequest()).catch((e) => showError(e.message));
});
$("#over-dialog").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeGameOver(); // click on the backdrop
});
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]") || e.target.matches("input, textarea")) return;
  if (!state) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
  else if (e.ctrlKey || e.metaKey || e.altKey) return;
  else if (e.key === "f") flip();
  else if (e.key === "m") toggleSound();
  else if (e.key === "Escape") {
    hidePromotion();
    selected = null;
    render();
  }
});
window.addEventListener("resize", () => {
  if (!promoEl.hidden) { hidePromotion(); selected = null; render(); }
});
$("#new-dialog").addEventListener("cancel", () => { if (!state) renderIntro(); });

async function start() {
  renderSoundButton();
  try {
    options = await api("/options");
    const saved = loadPref("game");
    if (saved) {
      try {
        const s = await api(`/games/${saved}`);
        if (!s.outcome) {     // resume a game in progress
          state = s;
          lastRequest = savedRequest();
          render();
          if (state.bot_to_move) botMove();
          return;
        }
      } catch { /* server restarted: nothing to resume */ }
    }
    renderIntro();
  } catch (e) {
    $("#intro").hidden = false;
    $("#intro h2").textContent = "Can't reach the server";
    $("#intro p").textContent = "Start it with: python3 run.py";
    $("#btn-start").hidden = true;
    showError(e.message);
  }
}

start();

# MidKnight

A playable web version of **MidKnight**, the 5×6 chess variant from Matthew's
Stat 234 final paper (*ELOquent: Analysis of Self-Play Reinforcement Learning
and Concept Detection for Novel Chess Variants*, Harvard, spring 2024). It
builds the paper's stated future work: an interface to play the board 1v1 or
against the engine, with difficulty levels.

This is Matthew's own repository. Everything here is written fresh for it.

## Goal

- Play MidKnight in the browser: click a piece, then click a square to move it.
  Legal moves are highlighted, and the game detects check, checkmate and
  stalemate.
- **1v1** on one screen (hot-seat), and **vs computer**.
- Several bots, chosen in a New game dialog, from weak to strong (see **Bots**).
- Seeds replay a game exactly, including the bot's choices.

## The game

Starting position (White at the bottom, files a–e, ranks 1–6):

```
6  r n k n r
5  p p p p p
4  . . . . .
3  . . . . .
2  P P P P P
1  R N K N R
   a b c d e
```

Each side has a king, 2 knights, 2 rooks and 5 pawns. There are no queens or
bishops. Moves follow normal chess.

**Rules (confirmed with Matthew, 2026-11-11).** There is no castling (the
original setup has no `.castle` file). Two options are chosen per game in the
New game dialog. Both are kept because the network only knows the first set,
and Matthew may retrain it on the second:

- Promotion: N/B/R/Q (training rules, the default) or N/R only.
- Pawn's first move: two squares with en passant (training rules, the
  default) or one square only.

Draws, as in training: stalemate; insufficient material (no pawn, rook or
queen, and at most one minor each); and no progress, once 21 plies pass with
no pawn move or capture. There is no threefold repetition. One deliberate
difference from the old simulator: checkmate on that 21st ply is a win. The original simulator,
`minichess/chess/fastchess.py` in the old project (see **Existing material**),
shows exactly what the trained network was trained on. Read it to match those
rules, but do not copy its code.

## Architecture

These patterns worked well in an earlier project (a Lost Cities card game with
bots). Reuse them:

```
run.py                 start a local server and open the browser
midknight/engine.py    the rules only: board, moves, check, game over. No I/O.
midknight/bots/        one strategy per file, discovered automatically
    base.py            Bot base class: name, label, description, options, act()
midknight/server.py    small HTTP server (standard library) with a JSON API
web/                   index.html, style.css, app.js: draws what the server sends
tests/                 rule tests, tests every bot must pass, server tests
```

- **The engine is pure.** The bots, the server and the tests share one
  implementation of the rules, and the page never re-implements them. The
  server holds the game; the page only draws what it is sent and posts moves.
- **Bots are auto-registered.** Every `Bot` subclass in `midknight/bots/` is
  listed in the dialog. Files starting with `_` are helpers and are skipped.
  A bot gets the position and the legal moves and returns one of them. Bots
  can declare settings (an int range or a fixed list of choices) that show up
  under the bot picker.
- **Seeds.** One seed fixes every random choice; bots use their own seeded
  `random.Random`. Search budgets count iterations or nodes, never seconds:
  time-based budgets break replay.
- **Tests every bot must pass:** whole games are played legally, a seed
  reproduces the game, and the bot never returns an illegal move. Bots that
  take a while are marked `slow`, and their whole-game tests run only with
  `pytest -m slow`.
- **Few dependencies.** Python 3.10+ with only the standard library for the
  game, server and classical bots. TensorFlow, only for the neural bot, is an
  optional extra, and the game must start without it.
- The UI uses Matthew's piece artwork (see **Existing material**). Include the
  usual chess comforts: last-move highlight, a check indicator, a move list in
  algebraic notation, flip board, undo in 1v1, and a promotion picker.

## Bots

From weakest to strongest:

1. **Random**: any legal move. Useful for tests.
2. **Classical engine** (the paper's section 2): negamax with alpha-beta
   pruning, iterative deepening, move ordering (captures first, MVV-LVA,
   killer moves), quiescence search on captures, and a transposition table
   keyed by a Zobrist hash. The value function uses the paper's features:
   material (pawn 1, knight 2, rook 4, as in the paper), king safety and
   activity, piece mobility and synergy, and passed pawns. Depth or node
   budget is the difficulty setting.
3. **Learned value function** (paper sections 3.1–3.2, TD-Leaf style). The
   same search, with evaluation weights trained by self-play: fit TD(λ)
   returns with a ridge penalty, and include a replay buffer.
4. **ELOquent network** (paper section 4): Matthew's trained AlphaZero-style
   network driving MCTS with the PUCT/UCB1 rule from the paper. Training
   epochs make natural difficulty levels: early epochs play weakly, epoch 302
   plays best. This is the paper's "different epochs for different levels"
   idea. The network expects the input encoding of the original code base;
   reproduce that encoding in a fresh module and check it against the old
   code's outputs on a few positions.

## Lessons from building search bots before

- Search on a compact state with apply/undo, not copies. Test that undo
  restores the state exactly after random move sequences. A reordering bug in
  undo once crashed alpha-beta while the shallower searches hid it.
- Test that move generation matches the rules engine exactly, position by
  position.
- Measure how long one evaluation takes early. In pure Python, every
  microsecond of evaluation decides how deep the search can go.
- **Training a value function:**
  - Keep a replay buffer of the last few generations of games. Fitting only
    the newest generation made the weights oscillate.
  - Merge features that are nearly collinear, and regularise.
  - Choose weights by head-to-head games, not by prediction error.
- **Benchmarking:** play paired games (the same openings with colours swapped)
  in parallel across all CPU cores, and report wins-losses-draws with a
  standard error. Differences under about 2 standard errors are noise.
- A sharp policy prior (low softmax temperature) helped MCTS a lot. The
  exploration constant mattered less.

## Existing material (read-only; on Matthew's disk, not in this repo)

Folder: `~/Desktop/Harvarchive/Spring '24/Stat 234/Final Project Stat 234/`

- `Final Paper Stat 234.pdf`: the paper; sections 2 and 4 describe the
  methods above.
- `chess pieces/`: 12 piece SVGs (`wK.svg` … `bR.svg`). These are Matthew's
  assets for the UI and may be copied into `web/`.
- `5by6board.png`, `5by6_board.jpg`, `Board.psd`: board artwork from the
  poster.
- `explainable-minichess-main/`: the training code base by Hammersborg &
  Strümke (arXiv 2211.05500), which Matthew used for training. **It has no
  LICENSE file, so do not copy its code into this repository.** Reading it to
  learn the rules and the network's input encoding is fine.
  - `minichess/boards/5x6matthew.board`: the MidKnight starting position.
  - `configs/base.json`: training settings (500 MCTS simulations per move,
    cpuct 1.4, replay buffer of 30,000).
  - `minichess/agents/checkpoints/5x6matthew/eloquent/<epoch>/`: the trained
    network, as Keras/TensorFlow 2.10 SavedModels for epochs 0–302 (163 MB in
    all), with `<epoch>.json` training losses. These are Matthew's results.
    Copy only the few epochs used as difficulty levels, and keep large files
    out of git (Git LFS or a download step) if the repository is public.

## Working agreements

- Commit only when asked; never force-push. Use a branch for each feature.
- Keep the README current: how to play, the bots and their settings, how to
  run tests.

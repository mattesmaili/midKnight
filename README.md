# MidKnight

A playable web version of **MidKnight**, a 5×6 chess variant with only kings,
knights, rooks and pawns. It comes from the Stat 234 paper *ELOquent: Analysis
of Self-Play Reinforcement Learning and Concept Detection for Novel Chess
Variants* (Harvard, spring 2024).

```
6  r n k n r
5  p p p p p
4  . . . . .
3  . . . . .
2  P P P P P
1  R N K N R
   a b c d e
```

## Play

Needs Python 3.10 or newer and nothing else.

```sh
python3 run.py                 # opens http://127.0.0.1:8234
python3 run.py --port 9000 --no-browser
```

The page opens on the starting position with a **Start a game** button. The
New game dialog asks who plays (the computer, or 1v1 on one screen), the
opponent and its level, your colour, the rules, and optionally a seed. Then drag a piece to a highlighted square, or click the piece and then click the
square. A pawn that reaches the last rank opens a picker for the promotion
piece. Dropping a piece off its legal squares puts it back. Click the selected
piece again, or press Esc, to cancel.

Moves, captures, check, the start and the end of a game each have their own sound;
the speaker button (or M) turns sound off. When the game ends, a pop-up
announces the winner and offers a rematch with the same rules.

- **New game** chooses the players and the rule options below.
- **Undo** (Ctrl/⌘+Z) takes back the last move; against the computer it takes
  back your last move and the computer's reply.
- **Flip** (F) turns the board around.
- The page remembers the current game, so reloading the tab resumes it until
  the server is restarted.

## Rules

Normal chess moves for the king, knights, rooks and pawns. There is no
castling. Two rules are chosen per game in the New game dialog:

| Option | Choices |
| --- | --- |
| Promotion | **Knight, bishop, rook or queen** (default; the rules the ELOquent network was trained on) or **knight or rook only** |
| Pawn's first move | **Two squares, with en passant** (default; training rules) or **one square only** |

The game ends in:

- **checkmate**;
- **stalemate**: a draw;
- **insufficient material**: a draw when neither side has a pawn, rook or
  queen, and each has at most one knight or bishop;
- **no progress**: a draw once 21 plies (half-moves) in a row pass with no
  pawn move and no capture. This is the original training simulator's rule.
  It fits the small board better than the 50-move rule. A checkmate on that
  21st ply still counts as a win.

There is no threefold-repetition rule.

The move generator was checked against the original training simulator: perft
14 / 185 / 2,559 / 33,383 from the start, plus every position of 120 random
games (see `tests/data/oldsim_ref.json`).

## Bots

Pick one in the New game dialog. From weakest to strongest:

| Bot | Settings | How it plays |
| --- | --- | --- |
| **Random** | none | Any legal move. |
| **Classical engine** | Level 1–8 | The paper's section 2: negamax with alpha-beta pruning, iterative deepening, a transposition table (Zobrist hash), move ordering (MVV-LVA captures, killer moves, history), quiescence search and a check extension. The evaluation is a weighted sum of the paper's features: material (pawn 1, knight 2, rook 4), king safety and activity, mobility, piece synergy (connected rooks, open files, supported knights) and passed pawns. |

Classical levels: 1–2 search one ply and add deliberate evaluation noise, 3
searches 2 plies with a little noise, and 4–8 search 3, 4, 5, 6 and 8 plies
without noise. Each level has a node budget, so level 8 takes at most a few
seconds a move. Each level beats the one below it (paired matches, 80 games unless noted):

| Match | Score for the higher level |
| --- | --- |
| level 1 vs random | 0.975 ± 0.012 |
| level 2 vs level 1 | 0.675 ± 0.040 |
| level 3 vs level 2 | 0.812 ± 0.028 |
| level 4 vs level 3 | 0.719 ± 0.033 |
| level 5 vs level 4 | 0.637 ± 0.032 |
| level 6 vs level 5 (60 games) | 0.650 ± 0.037 |
| level 7 vs level 6 (40 games) | 0.588 ± 0.052 |

**Seeds.** Every game has a seed, shown under the move list. A bot's move
depends only on the seed, its settings and the position, so the same seed
and the same moves replay a game exactly, even after undos. Enter a seed in
the New game dialog to replay one.

**Adding a bot.** Put a `Bot` subclass in a new file in `midknight/bots/`
(files starting with `_` are helpers and are skipped). It is listed in the
dialog automatically. Give it a `name`, `label`, `description`, `strength`
(for ordering) and optional `options` (`IntOption` or `ChoiceOption`), and
implement `act(position, legal)` to return one of `legal`, using `self.rng`
for any randomness. Search budgets must count nodes, not seconds.

**Benchmarking.** `tools/match.py` plays paired games (the same random opening
with colours swapped) on all CPU cores and reports wins, losses and draws with
a standard error:

```sh
.venv/bin/python tools/match.py classical:level=5 classical:level=4 --pairs 40
```

Still to come: a learned value function (TD-Leaf, paper section 3) and the
ELOquent network with MCTS (paper section 4).

## Credits

- Fonts: Fredoka and Nunito, self-hosted under the SIL Open Font License
  (`web/fonts/`).
- Sounds: chess-piece recordings from Freesound (CC0), plus synthesized
  woodblock and chime accents (`web/sounds/CREDITS.md`). To use other sounds, replace a file
  and keep its name.

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest            # fast tests
.venv/bin/python -m pytest -m slow    # long tests (perft depth 4, whole games for slow bots)
```

## Layout

```
run.py                 start the server and open the browser
midknight/engine.py    the rules only: board, moves, check, game over, notation
midknight/bots/        one bot per file, discovered automatically (base.py: the Bot class)
midknight/server.py    standard-library HTTP server with a JSON API
tools/match.py         paired bot-vs-bot matches on all cores
web/                   index.html, style.css, app.js, piece artwork, fonts, sounds
tests/                 rule tests, a cross-check with the original simulator, server tests
```

The server holds each game. The page only draws the state it receives and
posts moves, so the rules exist in one place. API:

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/options` | |
| POST | `/api/games` | `{"rules": {...}, "mode": "1v1" or "bot", "bot": {"name": "classical", "settings": {"level": 4}}, "color": "w"/"b"/"random", "seed": 123}` |
| GET | `/api/games/<id>` | |
| POST | `/api/games/<id>/move` | `{"move": "c2c3"}` (UCI; promotion as a 5th letter, e.g. `b5a6n`) |
| POST | `/api/games/<id>/bot` | the computer plays its move |
| POST | `/api/games/<id>/undo` | |

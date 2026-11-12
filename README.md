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

The page opens on the starting position with a **Start a game** button, which
asks for the rules. Then drag a piece to a highlighted square, or click the piece and then click the
square. A pawn that reaches the last rank opens a picker for the promotion
piece. Dropping a piece off its legal squares puts it back. Click the selected
piece again, or press Esc, to cancel.

Moves, captures, check, the start and the end of a game each have their own sound;
the speaker button (or M) turns sound off. When the game ends, a pop-up
announces the winner and offers a rematch with the same rules.

- **New game** chooses the rule options below. For now the only mode is 1v1
  on one screen (hot-seat).
- **Undo** (Ctrl/⌘+Z) takes back the last move.
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

Not built yet. Planned, from weakest to strongest: random, a classical
alpha-beta engine, a learned value function (TD-Leaf), and the ELOquent
network with MCTS. See `CLAUDE.md`.

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
.venv/bin/python -m pytest -m slow    # long tests (perft depth 4)
```

## Layout

```
run.py                 start the server and open the browser
midknight/engine.py    the rules only: board, moves, check, game over, notation
midknight/server.py    standard-library HTTP server with a JSON API
web/                   index.html, style.css, app.js, piece artwork, fonts, sounds
tests/                 rule tests, a cross-check with the original simulator, server tests
```

The server holds each game. The page only draws the state it receives and
posts moves, so the rules exist in one place. API:

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/options` | |
| POST | `/api/games` | `{"rules": {"promotions": "NBRQ" or "NR", "double_push": true}}` |
| GET | `/api/games/<id>` | |
| POST | `/api/games/<id>/move` | `{"move": "c2c3"}` (UCI; promotion as a 5th letter, e.g. `b5a6n`) |
| POST | `/api/games/<id>/undo` | |

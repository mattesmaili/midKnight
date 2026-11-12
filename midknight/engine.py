"""The rules of MidKnight, and nothing else (no I/O).

MidKnight is chess on a 5x6 board (files a-e, ranks 1-6). Each side has a
king, two knights, two rooks and five pawns. There is no castling.

Two rule options are chosen per game (see ``Rules``):

* which pieces a pawn may promote to: N, B, R, Q (the rules the ELOquent
  network was trained on) or just N, R;
* whether a pawn's first move may go two squares (with en passant).

Draws: stalemate, insufficient material, and the no-progress rule of the
original simulator: the game is drawn once 21 plies in a row pass without a
pawn move or a capture. Checkmate takes precedence over the no-progress rule.

Squares are numbered ``rank * 5 + file`` with a1 = 0, e1 = 4, a6 = 25.
Pieces are ints: positive for White, negative for Black. A move is a tuple
``(from_sq, to_sq, promo)`` where ``promo`` is a piece type or 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, NamedTuple, Optional

FILES = 5
RANKS = 6
SQUARES = FILES * RANKS
FILE_NAMES = "abcde"

WHITE, BLACK = 1, -1
EMPTY, PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = range(7)

PIECE_LETTERS = {PAWN: "P", KNIGHT: "N", BISHOP: "B", ROOK: "R", QUEEN: "Q", KING: "K"}
LETTER_PIECES = {v: k for k, v in PIECE_LETTERS.items()}

START_FEN = "rnknr/ppppp/5/5/PPPPP/RNKNR w - - 0 1"

#: Plies without a pawn move or capture after which the game is drawn.
NO_PROGRESS_LIMIT = 21

Move = tuple  # (from_sq, to_sq, promo)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rules:
    """Per-game rule options."""

    promotions: tuple = (KNIGHT, BISHOP, ROOK, QUEEN)
    double_push: bool = True

    PROMOTION_SETS = {"NBRQ": (KNIGHT, BISHOP, ROOK, QUEEN), "NR": (KNIGHT, ROOK)}

    @property
    def promotion_set(self) -> str:
        return "".join(PIECE_LETTERS[p] for p in self.promotions)

    def to_dict(self) -> dict:
        return {"promotions": self.promotion_set, "double_push": self.double_push}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Rules":
        d = d or {}
        key = str(d.get("promotions", "NBRQ")).upper()
        if key not in cls.PROMOTION_SETS:
            raise ValueError(f"unknown promotion set {key!r}; choose NBRQ or NR")
        return cls(promotions=cls.PROMOTION_SETS[key], double_push=bool(d.get("double_push", True)))


#: The rules the ELOquent network was trained on.
TRAINING_RULES = Rules()


# --------------------------------------------------------------------------
# Square helpers and precomputed tables
# --------------------------------------------------------------------------

def sq(file: int, rank: int) -> int:
    return rank * FILES + file


def file_of(s: int) -> int:
    return s % FILES


def rank_of(s: int) -> int:
    return s // FILES


def square_name(s: int) -> str:
    return FILE_NAMES[file_of(s)] + str(rank_of(s) + 1)


def parse_square(name: str) -> int:
    if len(name) != 2 or name[0] not in FILE_NAMES or name[1] not in "123456":
        raise ValueError(f"bad square {name!r}")
    return sq(FILE_NAMES.index(name[0]), int(name[1]) - 1)


def _steps(deltas) -> list:
    table = []
    for s in range(SQUARES):
        f, r = file_of(s), rank_of(s)
        table.append(tuple(sq(f + df, r + dr) for df, dr in deltas
                           if 0 <= f + df < FILES and 0 <= r + dr < RANKS))
    return table


def _rays(directions) -> list:
    table = []
    for s in range(SQUARES):
        rays = []
        for df, dr in directions:
            f, r, ray = file_of(s) + df, rank_of(s) + dr, []
            while 0 <= f < FILES and 0 <= r < RANKS:
                ray.append(sq(f, r))
                f, r = f + df, r + dr
            if ray:
                rays.append(tuple(ray))
        table.append(tuple(rays))
    return table


KNIGHT_STEPS = _steps([(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)])
KING_STEPS = _steps([(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)])
STRAIGHT_RAYS = _rays([(1, 0), (-1, 0), (0, 1), (0, -1)])
DIAGONAL_RAYS = _rays([(1, 1), (1, -1), (-1, 1), (-1, -1)])
# PAWN_CAPTURES[color][s]: squares a pawn of `color` on s attacks.
PAWN_CAPTURES = {
    WHITE: _steps([(-1, 1), (1, 1)]),
    BLACK: _steps([(-1, -1), (1, -1)]),
}


# --------------------------------------------------------------------------
# Position
# --------------------------------------------------------------------------

class Outcome(NamedTuple):
    result: str   # "1-0", "0-1" or "1/2-1/2"
    reason: str   # "checkmate", "stalemate", "no progress", "insufficient material"

    @property
    def winner(self) -> Optional[int]:
        return {"1-0": WHITE, "0-1": BLACK}.get(self.result)


class Position:
    """A mutable position with ``push``/``pop`` (apply and undo in place)."""

    __slots__ = ("board", "turn", "ep", "clock", "ply", "kings", "rules", "_stack")

    def __init__(self, fen: str = START_FEN, rules: Rules = TRAINING_RULES):
        self.rules = rules
        self._stack: list = []
        self._load_fen(fen)

    # ---- FEN ------------------------------------------------------------

    def _load_fen(self, fen: str) -> None:
        parts = fen.split()
        if not 2 <= len(parts) <= 6:
            raise ValueError("FEN needs: placement turn [castling ep clock fullmove], got " + repr(fen))
        parts += ["-", "-", "0", "1"][len(parts) - 2:]
        rows = parts[0].split("/")
        if len(rows) != RANKS:
            raise ValueError(f"FEN needs {RANKS} ranks")
        board = [EMPTY] * SQUARES
        kings = {WHITE: -1, BLACK: -1}
        for i, row in enumerate(rows):
            rank, f = RANKS - 1 - i, 0
            for c in row:
                if c.isdigit():
                    f += int(c)
                    continue
                if c.upper() not in LETTER_PIECES or f >= FILES:
                    raise ValueError(f"bad FEN rank {row!r}")
                color = WHITE if c.isupper() else BLACK
                board[sq(f, rank)] = color * LETTER_PIECES[c.upper()]
                if c.upper() == "K":
                    kings[color] = sq(f, rank)
                f += 1
            if f != FILES:
                raise ValueError(f"bad FEN rank {row!r}")
        if kings[WHITE] < 0 or kings[BLACK] < 0:
            raise ValueError("each side needs a king")
        self.board = board
        self.kings = kings
        self.turn = WHITE if parts[1] == "w" else BLACK
        if parts[1] not in ("w", "b"):
            raise ValueError("FEN turn must be w or b")
        self.ep = -1 if parts[3] == "-" else parse_square(parts[3])
        self.clock = int(parts[4])
        fullmove = int(parts[5])
        self.ply = 2 * (fullmove - 1) + (0 if self.turn == WHITE else 1)

    def fen(self) -> str:
        rows = []
        for rank in range(RANKS - 1, -1, -1):
            row, empty = "", 0
            for f in range(FILES):
                p = self.board[sq(f, rank)]
                if p == EMPTY:
                    empty += 1
                    continue
                if empty:
                    row, empty = row + str(empty), 0
                letter = PIECE_LETTERS[abs(p)]
                row += letter if p > 0 else letter.lower()
            rows.append(row + (str(empty) if empty else ""))
        ep = square_name(self.ep) if self.ep >= 0 else "-"
        return f"{'/'.join(rows)} {'w' if self.turn == WHITE else 'b'} - {ep} {self.clock} {self.ply // 2 + 1}"

    def copy(self) -> "Position":
        p = Position.__new__(Position)
        p.board = self.board[:]
        p.turn, p.ep, p.clock, p.ply = self.turn, self.ep, self.clock, self.ply
        p.kings = dict(self.kings)
        p.rules = self.rules
        p._stack = []
        return p

    def key(self) -> tuple:
        """Hashable identity of the position (for tests and caches)."""
        return (tuple(self.board), self.turn, self.ep, self.clock)

    # ---- attacks ----------------------------------------------------------

    def is_attacked(self, s: int, by: int) -> bool:
        """Is square ``s`` attacked by a piece of colour ``by``?"""
        b = self.board
        for t in KNIGHT_STEPS[s]:
            if b[t] == by * KNIGHT:
                return True
        for t in KING_STEPS[s]:
            if b[t] == by * KING:
                return True
        # A pawn of `by` attacks s if it sits where a pawn of the other colour
        # on s would capture.
        for t in PAWN_CAPTURES[-by][s]:
            if b[t] == by * PAWN:
                return True
        rook, queen, bishop = by * ROOK, by * QUEEN, by * BISHOP
        for ray in STRAIGHT_RAYS[s]:
            for t in ray:
                p = b[t]
                if p:
                    if p == rook or p == queen:
                        return True
                    break
        for ray in DIAGONAL_RAYS[s]:
            for t in ray:
                p = b[t]
                if p:
                    if p == bishop or p == queen:
                        return True
                    break
        return False

    def in_check(self, color: Optional[int] = None) -> bool:
        color = self.turn if color is None else color
        return self.is_attacked(self.kings[color], -color)

    # ---- move generation --------------------------------------------------

    def pseudo_legal_moves(self) -> list:
        b, me = self.board, self.turn
        moves = []
        promo_rank = RANKS - 1 if me == WHITE else 0
        start_rank = 1 if me == WHITE else RANKS - 2
        fwd = FILES * me
        promos = self.rules.promotions
        for s in range(SQUARES):
            p = b[s] * me
            if p <= 0:
                continue
            if p == PAWN:
                targets = []
                t = s + fwd
                if b[t] == EMPTY:
                    targets.append(t)
                    t2 = t + fwd
                    if self.rules.double_push and rank_of(s) == start_rank and b[t2] == EMPTY:
                        moves.append((s, t2, 0))
                for t in PAWN_CAPTURES[me][s]:
                    if b[t] * me < 0 or t == self.ep:
                        targets.append(t)
                for t in targets:
                    if rank_of(t) == promo_rank:
                        moves.extend((s, t, pr) for pr in promos)
                    else:
                        moves.append((s, t, 0))
            elif p == KNIGHT or p == KING:
                for t in (KNIGHT_STEPS if p == KNIGHT else KING_STEPS)[s]:
                    if b[t] * me <= 0:
                        moves.append((s, t, 0))
            else:
                rays = ()
                if p == ROOK or p == QUEEN:
                    rays += STRAIGHT_RAYS[s]
                if p == BISHOP or p == QUEEN:
                    rays += DIAGONAL_RAYS[s]
                for ray in rays:
                    for t in ray:
                        q = b[t] * me
                        if q > 0:
                            break
                        moves.append((s, t, 0))
                        if q < 0:
                            break
        return moves

    def legal_moves(self) -> list:
        me = self.turn
        legal = []
        for m in self.pseudo_legal_moves():
            self.push(m)
            if not self.is_attacked(self.kings[me], -me):
                legal.append(m)
            self.pop()
        return legal

    def has_legal_move(self) -> bool:
        me = self.turn
        for m in self.pseudo_legal_moves():
            self.push(m)
            ok = not self.is_attacked(self.kings[me], -me)
            self.pop()
            if ok:
                return True
        return False

    # ---- apply / undo -----------------------------------------------------

    def push(self, move: Move) -> None:
        """Apply a (pseudo-)legal move in place. Undo with ``pop``."""
        frm, to, promo = move
        b, me = self.board, self.turn
        piece = b[frm]
        captured = b[to]
        cap_sq = to
        is_pawn = piece == me * PAWN
        if is_pawn and to == self.ep and captured == EMPTY:
            cap_sq = to - FILES * me          # en passant
            captured = b[cap_sq]
            b[cap_sq] = EMPTY
        self._stack.append((frm, to, piece, captured, cap_sq, self.ep, self.clock))
        b[to] = me * promo if promo else piece
        b[frm] = EMPTY
        if piece == me * KING:
            self.kings[me] = to
        self.clock = 0 if (is_pawn or captured) else self.clock + 1
        self.ep = (frm + to) // 2 if is_pawn and abs(to - frm) == 2 * FILES else -1
        self.turn = -me
        self.ply += 1

    def pop(self) -> None:
        frm, to, piece, captured, cap_sq, ep, clock = self._stack.pop()
        b = self.board
        self.turn = me = -self.turn
        self.ply -= 1
        self.ep, self.clock = ep, clock
        b[frm] = piece
        b[to] = EMPTY
        b[cap_sq] = captured
        if piece == me * KING:
            self.kings[me] = frm

    # ---- game end ---------------------------------------------------------

    def insufficient_material(self) -> bool:
        """Neither side has a pawn, rook or queen, and each has at most one minor."""
        minors = {WHITE: 0, BLACK: 0}
        for p in self.board:
            t = abs(p)
            if t in (PAWN, ROOK, QUEEN):
                return False
            if t in (KNIGHT, BISHOP):
                c = WHITE if p > 0 else BLACK
                minors[c] += 1
                if minors[c] > 1:
                    return False
        return True

    def outcome(self) -> Optional[Outcome]:
        """The game's result if it is over, else None."""
        if not self.has_legal_move():
            if self.in_check():
                return Outcome("0-1" if self.turn == WHITE else "1-0", "checkmate")
            return Outcome("1/2-1/2", "stalemate")
        if self.insufficient_material():
            return Outcome("1/2-1/2", "insufficient material")
        if self.clock >= NO_PROGRESS_LIMIT:
            return Outcome("1/2-1/2", "no progress")
        return None

    # ---- notation ---------------------------------------------------------

    def parse_uci(self, text: str) -> Move:
        text = text.strip().lower()
        if len(text) not in (4, 5):
            raise ValueError(f"bad move {text!r}")
        promo = 0
        if len(text) == 5:
            if text[4].upper() not in "NBRQ":
                raise ValueError(f"bad promotion piece in {text!r}")
            promo = LETTER_PIECES[text[4].upper()]
        return (parse_square(text[:2]), parse_square(text[2:4]), promo)

    def san(self, move: Move, legal: Optional[list] = None) -> str:
        """Standard algebraic notation for a legal ``move`` in this position."""
        frm, to, promo = move
        legal = self.legal_moves() if legal is None else legal
        piece = abs(self.board[frm])
        is_capture = self.board[to] != EMPTY or (piece == PAWN and to == self.ep)
        if piece == PAWN:
            text = (FILE_NAMES[file_of(frm)] + "x" if is_capture else "") + square_name(to)
            if promo:
                text += "=" + PIECE_LETTERS[promo]
        else:
            text = PIECE_LETTERS[piece]
            rivals = [m[0] for m in legal
                      if m[1] == to and m[0] != frm and abs(self.board[m[0]]) == piece]
            if rivals:
                if all(file_of(r) != file_of(frm) for r in rivals):
                    text += FILE_NAMES[file_of(frm)]
                elif all(rank_of(r) != rank_of(frm) for r in rivals):
                    text += str(rank_of(frm) + 1)
                else:
                    text += square_name(frm)
            text += ("x" if is_capture else "") + square_name(to)
        self.push(move)
        if self.in_check():
            text += "+" if self.has_legal_move() else "#"
        self.pop()
        return text


def move_to_uci(move: Move) -> str:
    frm, to, promo = move
    return square_name(frm) + square_name(to) + (PIECE_LETTERS[promo].lower() if promo else "")


# --------------------------------------------------------------------------
# Game: a position plus its history, as the server and bots use it
# --------------------------------------------------------------------------

@dataclass
class Game:
    rules: Rules = TRAINING_RULES
    start_fen: str = START_FEN
    position: Position = field(init=False)
    moves: list = field(default_factory=list)   # [(move, san)]

    def __post_init__(self):
        self.position = Position(self.start_fen, self.rules)

    def legal_moves(self) -> list:
        return self.position.legal_moves() if self.outcome() is None else []

    def outcome(self) -> Optional[Outcome]:
        return self.position.outcome()

    def play(self, move) -> str:
        """Play a move (tuple or UCI string). Returns its SAN. Raises ValueError if illegal."""
        if isinstance(move, str):
            move = self.position.parse_uci(move)
        move = tuple(move)
        if self.outcome() is not None:
            raise ValueError("the game is over")
        legal = self.position.legal_moves()
        if move not in legal:
            raise ValueError(f"illegal move {move_to_uci(move)}")
        san = self.position.san(move, legal)
        self.position.push(move)
        self.moves.append((move, san))
        return san

    def undo(self) -> Optional[Move]:
        if not self.moves:
            return None
        move, _ = self.moves.pop()
        self.position.pop()
        return move

    def replay(self, ucis: Iterable[str]) -> None:
        for u in ucis:
            self.play(u)

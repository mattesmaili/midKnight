"""Negamax with alpha-beta pruning (the paper's section 2), plus the usual help:

* iterative deepening, with the previous iteration's best move searched first;
* move ordering: transposition-table move, captures by MVV-LVA, promotions,
  two killer moves per ply, then a history score;
* quiescence search on captures and promotions (all moves when in check);
* a transposition table keyed by a Zobrist hash (which includes the
  no-progress clock, because it changes the result near the 21-ply limit);
* a check extension.

The budget is a node count, so a search with the same inputs always returns
the same move. Scores are centipawns from the side to move's point of view;
mate scores are ``MATE - plies``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..engine import EMPTY, NO_PROGRESS_LIMIT, PAWN, SQUARES, Position

MATE = 1_000_000
MATE_BOUND = MATE - 1000
INF = 10 ** 9
EXACT, LOWER, UPPER = 0, 1, 2
MAX_PLY = 64
QS_MAX_PLY = 12
TT_LIMIT = 400_000

_VALUE = [0, 100, 200, 200, 400, 600, 0]  # by piece type, for MVV-LVA

_zrng = random.Random(0x5EED)
ZOBRIST = [[_zrng.getrandbits(64) for _ in range(SQUARES)] for _ in range(13)]  # piece + 6
ZOBRIST_BLACK = _zrng.getrandbits(64)
ZOBRIST_EP = [_zrng.getrandbits(64) for _ in range(SQUARES)]
ZOBRIST_CLOCK = [_zrng.getrandbits(64) for _ in range(NO_PROGRESS_LIMIT + 2)]


def zobrist(pos: Position) -> int:
    h = 0
    b = pos.board
    for s in range(SQUARES):
        p = b[s]
        if p:
            h ^= ZOBRIST[p + 6][s]
    if pos.turn < 0:
        h ^= ZOBRIST_BLACK
    if pos.ep >= 0:
        h ^= ZOBRIST_EP[pos.ep]
    return h ^ ZOBRIST_CLOCK[min(pos.clock, NO_PROGRESS_LIMIT + 1)]


class SearchAborted(Exception):
    pass


@dataclass
class SearchResult:
    move: tuple
    score: int
    depth: int
    nodes: int
    pv: list = field(default_factory=list)


def is_capture(pos: Position, move) -> bool:
    frm, to, _ = move
    return pos.board[to] != EMPTY or (abs(pos.board[frm]) == PAWN and to == pos.ep)


class Search:
    def __init__(self, evaluate, max_depth: int, max_nodes: int, use_tt: bool = True,
                 use_ordering: bool = True, use_quiescence: bool = True):
        self.evaluate = evaluate
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.use_tt = use_tt
        self.use_ordering = use_ordering
        self.use_quiescence = use_quiescence
        self.tt: dict = {}
        self.killers = [[None, None] for _ in range(MAX_PLY + 1)]
        self.history: dict = {}
        self.nodes = 0

    # ---- root ----------------------------------------------------------------

    def run(self, pos: Position, legal: list, rng: random.Random | None = None) -> SearchResult:
        moves = list(legal)
        if rng is not None:
            rng.shuffle(moves)  # equal-scoring moves are then chosen at random (seeded)
        best = SearchResult(moves[0], 0, 0, 0)
        for depth in range(1, self.max_depth + 1):
            try:
                score, move, scores = self._root(pos, moves, depth, best.move)
            except SearchAborted:
                break
            best = SearchResult(move, score, depth, self.nodes, self._pv(pos, move, depth))
            # Re-order: best first, then by this iteration's scores.
            moves.sort(key=lambda m: -scores.get(m, -INF))
            if abs(score) >= MATE_BOUND:
                break  # a forced mate was found; deeper search can't change it
        best.nodes = self.nodes
        return best

    def _root(self, pos, moves, depth, first):
        ordered = [first] + [m for m in moves if m != first]
        alpha, beta = -INF, INF
        best_score, best_move = -INF, ordered[0]
        scores = {}
        for i, m in enumerate(ordered):
            pos.push(m)
            try:
                score = -self.negamax(pos, depth - 1, -beta, -alpha, 1)
            finally:
                pos.pop()
            scores[m] = score
            if score > best_score:
                best_score, best_move = score, m
            if score > alpha:
                alpha = score
        return best_score, best_move, scores

    def _pv(self, pos, move, depth):
        pv, seen = [move], set()
        p = pos.copy()
        p.push(move)
        for _ in range(depth - 1):
            entry = self.tt.get(zobrist(p))
            if not entry or entry[3] is None or entry[3] not in p.legal_moves():
                break
            key = zobrist(p)
            if key in seen:
                break
            seen.add(key)
            pv.append(entry[3])
            p.push(entry[3])
        return pv

    # ---- interior nodes ----------------------------------------------------------------

    def negamax(self, pos: Position, depth: int, alpha: int, beta: int, ply: int) -> int:
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise SearchAborted
        moves = pos.legal_moves()
        in_check = pos.in_check()
        if not moves:
            return -(MATE - ply) if in_check else 0
        if pos.clock >= NO_PROGRESS_LIMIT or pos.insufficient_material():
            return 0
        if in_check and ply < MAX_PLY - 1:
            depth += 1  # check extension
        if depth <= 0 or ply >= MAX_PLY:
            if self.use_quiescence:
                return self.quiesce(pos, alpha, beta, ply, 0, moves, in_check)
            return self.evaluate(pos)

        alpha0 = alpha
        key = tt_move = None
        if self.use_tt:
            key = zobrist(pos)
            entry = self.tt.get(key)
            if entry is not None:
                e_depth, flag, score, tt_move = entry
                if e_depth >= depth:
                    score = _from_tt(score, ply)
                    if flag == EXACT:
                        return score
                    if flag == LOWER and score > alpha:
                        alpha = score
                    elif flag == UPPER and score < beta:
                        beta = score
                    if alpha >= beta:
                        return score

        if self.use_ordering:
            moves = self._order(pos, moves, tt_move, ply)
        best, best_move = -INF, None
        for m in moves:
            pos.push(m)
            try:
                score = -self.negamax(pos, depth - 1, -beta, -alpha, ply + 1)
            finally:
                pos.pop()
            if score > best:
                best, best_move = score, m
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if self.use_ordering and not is_capture(pos, m) and not m[2]:
                            k = self.killers[ply]
                            if k[0] != m:
                                k[1], k[0] = k[0], m
                            hk = (pos.board[m[0]], m[1])
                            self.history[hk] = self.history.get(hk, 0) + depth * depth
                        break

        if self.use_tt:
            flag = UPPER if best <= alpha0 else LOWER if best >= beta else EXACT
            if len(self.tt) >= TT_LIMIT:
                self.tt.clear()
            self.tt[key] = (depth, flag, _to_tt(best, ply), best_move)
        return best

    def quiesce(self, pos, alpha, beta, ply, qply, moves=None, in_check=None) -> int:
        if moves is None:
            self.nodes += 1
            if self.nodes > self.max_nodes:
                raise SearchAborted
            moves = pos.legal_moves()
            in_check = pos.in_check()
            if not moves:
                return -(MATE - ply) if in_check else 0
            if pos.clock >= NO_PROGRESS_LIMIT or pos.insufficient_material():
                return 0
        if in_check and qply < QS_MAX_PLY:
            candidates = self._order(pos, moves, None, ply) if self.use_ordering else moves
            best = -INF
        else:
            stand = self.evaluate(pos)
            if stand >= beta or qply >= QS_MAX_PLY:
                return stand
            if stand > alpha:
                alpha = stand
            best = stand
            candidates = [m for m in moves if m[2] or is_capture(pos, m)]
            if self.use_ordering:
                candidates.sort(key=lambda m: -self._capture_score(pos, m))
        for m in candidates:
            pos.push(m)
            try:
                score = -self.quiesce(pos, -beta, -alpha, ply + 1, qply + 1)
            finally:
                pos.pop()
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        break
        return best

    # ---- move ordering ----------------------------------------------------------------

    def _capture_score(self, pos, m) -> int:
        frm, to, promo = m
        victim = abs(pos.board[to]) if pos.board[to] else (PAWN if abs(pos.board[frm]) == PAWN and to == pos.ep else 0)
        score = 0
        if victim:
            score += 10 * _VALUE[victim] - _VALUE[abs(pos.board[frm])] // 10 + 100_000
        if promo:
            score += 90_000 + _VALUE[promo]
        return score

    def _order(self, pos, moves, tt_move, ply):
        killers = self.killers[ply] if ply <= MAX_PLY else (None, None)
        history = self.history
        b = pos.board

        def key(m):
            if m == tt_move:
                return -10_000_000
            s = self._capture_score(pos, m)
            if s:
                return -s
            if m == killers[0]:
                return -80_000
            if m == killers[1]:
                return -79_000
            return -history.get((b[m[0]], m[1]), 0)

        return sorted(moves, key=key)


def _to_tt(score, ply):
    if score >= MATE_BOUND:
        return score + ply
    if score <= -MATE_BOUND:
        return score - ply
    return score


def _from_tt(score, ply):
    if score >= MATE_BOUND:
        return score - ply
    if score <= -MATE_BOUND:
        return score + ply
    return score

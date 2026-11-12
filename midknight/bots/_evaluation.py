"""A linear evaluation built from the features in the paper's section 2.

``features(pos)`` returns one number per name in ``FEATURES``, always as
White minus Black, so an evaluation is just a dot product with a weight
vector. The classical bot uses ``DEFAULT_WEIGHTS`` (hand-set, in pawns, with
the paper's material values pawn 1, knight 2, rook 4); the learned bot will
fit its own weights to the same features.

Features, grouped as in the paper:

* material: pawn, knight, bishop, rook, queen counts;
* king safety and activity: pawn shield and attacked squares around the king
  (weighted by how much material is left), king centralisation (weighted by
  how little is left);
* piece mobility and synergy: knight and slider mobility, connected rooks,
  rooks on open and semi-open files, knights defended by a pawn;
* pawns: passed pawns, how far they have advanced, doubled pawns;
* tempo: +1 for the side to move.
"""

from __future__ import annotations

from ..engine import (
    BISHOP, BLACK, DIAGONAL_RAYS, FILES, KING, KING_STEPS, KNIGHT, KNIGHT_STEPS,
    PAWN, PAWN_CAPTURES, QUEEN, RANKS, ROOK, SQUARES, STRAIGHT_RAYS, WHITE, Position,
)

FEATURES = (
    "pawn", "knight", "bishop", "rook", "queen",
    "king_shield", "king_attack", "king_center",
    "knight_mobility", "slider_mobility",
    "connected_rooks", "rook_open_file", "rook_semi_open_file", "supported_knight",
    "passed_pawn", "passed_advance", "doubled_pawn",
    "tempo",
)

DEFAULT_WEIGHTS = {
    "pawn": 1.0, "knight": 2.0, "bishop": 2.0, "rook": 4.0, "queen": 6.0,
    "king_shield": 0.15, "king_attack": -0.12, "king_center": 0.10,
    "knight_mobility": 0.05, "slider_mobility": 0.04,
    "connected_rooks": 0.15, "rook_open_file": 0.20, "rook_semi_open_file": 0.10,
    "supported_knight": 0.10,
    "passed_pawn": 0.25, "passed_advance": 0.15, "doubled_pawn": -0.20,
    "tempo": 0.05,
}

# Non-pawn material at the start, both sides together (2N + 2R each).
_PHASE_TOTAL = 2 * (2 * 2 + 2 * 4)
_PHASE_VALUE = {KNIGHT: 2, BISHOP: 2, ROOK: 4, QUEEN: 6}
_SQ_FILE = [s % FILES for s in range(SQUARES)]
_SQ_RANK = [s // FILES for s in range(SQUARES)]
# Distance from the centre of the 5x6 board (files c, ranks 3-4).
_CENTER_DIST = [abs(_SQ_FILE[s] - 2) + abs(_SQ_RANK[s] - 2.5) for s in range(SQUARES)]


def features(pos: Position) -> list:
    b = pos.board
    counts = {WHITE: [0] * 7, BLACK: [0] * 7}
    pawn_files = {WHITE: [0] * FILES, BLACK: [0] * FILES}
    pieces = {WHITE: [], BLACK: []}
    phase_material = 0
    for s in range(SQUARES):
        p = b[s]
        if p:
            c = WHITE if p > 0 else BLACK
            t = p * c
            counts[c][t] += 1
            pieces[c].append((t, s))
            if t == PAWN:
                pawn_files[c][_SQ_FILE[s]] += 1
            else:
                phase_material += _PHASE_VALUE.get(t, 0)
    mg = min(1.0, phase_material / _PHASE_TOTAL)
    eg = 1.0 - mg

    # Squares each side attacks, and mobility.
    attacked = {WHITE: [0] * SQUARES, BLACK: [0] * SQUARES}
    knight_mob = {WHITE: 0, BLACK: 0}
    slider_mob = {WHITE: 0, BLACK: 0}
    for c in (WHITE, BLACK):
        att = attacked[c]
        for t, s in pieces[c]:
            if t == PAWN:
                for x in PAWN_CAPTURES[c][s]:
                    att[x] = 1
            elif t == KNIGHT:
                for x in KNIGHT_STEPS[s]:
                    att[x] = 1
                    if b[x] * c <= 0:
                        knight_mob[c] += 1
            elif t == KING:
                for x in KING_STEPS[s]:
                    att[x] = 1
            else:
                rays = (STRAIGHT_RAYS[s] if t != BISHOP else ()) + (DIAGONAL_RAYS[s] if t != ROOK else ())
                for ray in rays:
                    for x in ray:
                        att[x] = 1
                        q = b[x] * c
                        if q <= 0:
                            slider_mob[c] += 1
                        if q:
                            break

    f = dict.fromkeys(FEATURES, 0.0)
    for c in (WHITE, BLACK):
        cnt, enemy = counts[c], -c
        f["pawn"] += c * cnt[PAWN]
        f["knight"] += c * cnt[KNIGHT]
        f["bishop"] += c * cnt[BISHOP]
        f["rook"] += c * cnt[ROOK]
        f["queen"] += c * cnt[QUEEN]
        f["knight_mobility"] += c * knight_mob[c]
        f["slider_mobility"] += c * slider_mob[c]

        # King safety (middlegame) and activity (endgame).
        k = pos.kings[c]
        kf, kr = _SQ_FILE[k], _SQ_RANK[k]
        shield = 0
        front = kr + c
        if 0 <= front < RANKS:
            for df in (-1, 0, 1):
                ff = kf + df
                if 0 <= ff < FILES and b[front * FILES + ff] == c * PAWN:
                    shield += 1
        zone = (k,) + KING_STEPS[k]
        attacked_zone = sum(attacked[enemy][x] for x in zone)
        f["king_shield"] += c * shield * mg
        f["king_attack"] += c * attacked_zone * mg
        f["king_center"] += c * (3.5 - _CENTER_DIST[k]) * eg

        # Piece synergy.
        rooks = [s for t, s in pieces[c] if t == ROOK]
        if len(rooks) >= 2 and _rooks_connected(b, rooks[0], rooks[1]):
            f["connected_rooks"] += c
        for s in rooks:
            file = _SQ_FILE[s]
            if pawn_files[c][file] == 0:
                if pawn_files[enemy][file] == 0:
                    f["rook_open_file"] += c
                else:
                    f["rook_semi_open_file"] += c
        for t, s in pieces[c]:
            if t == KNIGHT:
                for x in PAWN_CAPTURES[enemy][s]:   # squares a friendly pawn would defend s from
                    if b[x] == c * PAWN:
                        f["supported_knight"] += c
                        break

        # Pawn structure.
        for file in range(FILES):
            if pawn_files[c][file] > 1:
                f["doubled_pawn"] += c * (pawn_files[c][file] - 1)
        for t, s in pieces[c]:
            if t == PAWN and _is_passed(b, s, c):
                f["passed_pawn"] += c
                advance = (_SQ_RANK[s] - 1) if c == WHITE else (RANKS - 2 - _SQ_RANK[s])
                f["passed_advance"] += c * advance

    f["tempo"] = pos.turn
    return [f[name] for name in FEATURES]


def _rooks_connected(b, s1, s2) -> bool:
    if _SQ_RANK[s1] == _SQ_RANK[s2]:
        lo, hi = sorted((s1, s2))
        return all(b[x] == 0 for x in range(lo + 1, hi))
    if _SQ_FILE[s1] == _SQ_FILE[s2]:
        lo, hi = sorted((s1, s2))
        return all(b[x] == 0 for x in range(lo + FILES, hi, FILES))
    return False


def _is_passed(b, s, c) -> bool:
    file, rank = _SQ_FILE[s], _SQ_RANK[s]
    enemy_pawn = -c * PAWN
    r = rank + c
    while 0 <= r < RANKS:
        for df in (-1, 0, 1):
            ff = file + df
            if 0 <= ff < FILES and b[r * FILES + ff] == enemy_pawn:
                return False
        r += c
    return True


def weight_vector(weights: dict | None = None) -> list:
    w = DEFAULT_WEIGHTS if weights is None else weights
    return [float(w.get(name, 0.0)) for name in FEATURES]


def make_evaluator(weights: dict | None = None):
    """Return ``evaluate(pos) -> int``: centipawns from the side to move's view."""
    w = [100.0 * x for x in weight_vector(weights)]

    def evaluate(pos: Position) -> int:
        score = 0.0
        for wi, fi in zip(w, features(pos)):
            score += wi * fi
        return int(round(score)) * pos.turn

    return evaluate

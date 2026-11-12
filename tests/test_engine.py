"""Rule tests for midknight.engine."""

import hashlib
import json
import random
from pathlib import Path

import pytest

from midknight.engine import (
    BLACK, KNIGHT, QUEEN, ROOK, START_FEN, TRAINING_RULES, WHITE,
    Game, Position, Rules, move_to_uci, parse_square,
)

NR_SINGLE = Rules.from_dict({"promotions": "NR", "double_push": False})
REF = json.loads((Path(__file__).parent / "data" / "oldsim_ref.json").read_text())


def perft(pos, depth):
    """Leaf count; finished games count as 0 below the root (as in the old simulator)."""
    if depth == 0:
        return 1
    if pos.outcome() is not None and pos.ply > 0:
        return 0
    total = 0
    for m in pos.legal_moves():
        pos.push(m)
        total += perft(pos, depth - 1)
        pos.pop()
    return total


def ucis(pos):
    return sorted(move_to_uci(m) for m in pos.legal_moves())


# ---- starting position -------------------------------------------------------

def test_start_position_round_trips_fen():
    assert Position().fen() == START_FEN


def test_start_moves_training_rules():
    # 5 pawns x (1 or 2 squares) + 2 knights x 2 squares
    assert len(Position().legal_moves()) == 14


def test_start_moves_single_push():
    assert len(Position(rules=NR_SINGLE).legal_moves()) == 9


def test_perft_matches_original_simulator():
    pos = Position()
    assert [perft(pos, d) for d in range(1, 4)] == REF["perft"][:3]


@pytest.mark.slow
def test_perft_depth4_matches_original_simulator():
    assert perft(Position(), 4) == REF["perft"][3]


# ---- cross-check against the original simulator, position by position ---------

def test_random_games_match_original_simulator():
    """Replays the old simulator's random games; every legal move set must match."""
    for g in REF["games"]:
        pos = Position()
        moves = g["moves"].split()
        for i, (n, digest, result, in_check) in enumerate(g["positions"]):
            legal = ucis(pos)
            assert len(legal) == n, (g["moves"], i)
            assert hashlib.sha1(" ".join(legal).encode()).hexdigest()[:12] == digest, (g["moves"], i)
            assert pos.in_check() == bool(in_check)
            out = pos.outcome()
            if result is None:
                assert out is None
            else:
                expected = {1: "1-0", -1: "0-1", 0: "1/2-1/2"}[result]
                # One deliberate difference: here checkmate beats the no-progress draw.
                if not (out and out.reason == "checkmate" and result == 0):
                    assert out is not None and out.result == expected, (g["moves"], i, out)
            if i < len(moves):
                pos.push(pos.parse_uci(moves[i]))


# ---- apply / undo ------------------------------------------------------------

@pytest.mark.parametrize("rules", [TRAINING_RULES, NR_SINGLE])
def test_undo_restores_state_exactly(rules):
    rng = random.Random(7)
    for _ in range(40):
        pos = Position(rules=rules)
        keys, fens = [], []
        while pos.outcome() is None and pos.ply < 200:
            keys.append(pos.key())
            fens.append(pos.fen())
            pos.push(rng.choice(pos.legal_moves()))
        while keys:
            pos.pop()
            assert pos.key() == keys.pop()
            assert pos.fen() == fens.pop()
            assert pos.board[pos.kings[WHITE]] == 6 and pos.board[pos.kings[BLACK]] == -6


# ---- rule options --------------------------------------------------------------

def test_double_push_and_en_passant():
    pos = Position("rnknr/ppppp/5/1P3/P1PPP/RNKNR b - - 0 1")
    pos.push(pos.parse_uci("a5a3"))
    assert pos.fen().split()[3] == "a4"
    assert "b3a4" in ucis(pos)
    pos.push(pos.parse_uci("b3a4"))
    assert pos.board[parse_square("a3")] == 0  # captured pawn removed


def test_no_double_push_rule():
    pos = Position(rules=NR_SINGLE)
    assert not any(m[1] - m[0] == 10 for m in pos.legal_moves())


def test_promotion_choices():
    fen = "k4/2P2/5/5/5/4K w - - 0 1"
    assert sorted(m[2] for m in Position(fen).legal_moves() if m[0] == parse_square("c5")) == [2, 3, 4, 5]
    assert sorted(m[2] for m in Position(fen, NR_SINGLE).legal_moves() if m[0] == parse_square("c5")) == [KNIGHT, ROOK]


def test_rules_from_dict():
    assert Rules.from_dict(None) == TRAINING_RULES
    assert Rules.from_dict({"promotions": "nr", "double_push": False}) == NR_SINGLE
    with pytest.raises(ValueError):
        Rules.from_dict({"promotions": "Q"})


# ---- game end ------------------------------------------------------------------

def test_checkmate():
    pos = Position("k4/5/1K3/5/5/4R w - - 0 1")
    pos.push(pos.parse_uci("e1e6"))
    out = pos.outcome()
    assert out.reason == "checkmate" and out.result == "1-0"


def test_stalemate():
    pos = Position("k4/5/1K3/5/5/1R3 w - - 0 1")
    pos.push(pos.parse_uci("b1b5"))
    assert pos.outcome().reason == "stalemate"


def test_insufficient_material():
    assert Position("k4/5/5/5/5/N3K w - - 0 1").outcome().reason == "insufficient material"
    assert Position("k3n/5/5/5/5/N3K w - - 0 1").outcome().reason == "insufficient material"
    assert Position("k4/5/5/5/5/NN2K w - - 0 1").outcome() is None
    assert Position("k4/5/5/5/5/R3K w - - 0 1").outcome() is None


def test_no_progress_draw_after_21_plies():
    assert Position("k4/5/5/5/5/R3K w - - 20 1").outcome() is None
    assert Position("k4/5/5/5/5/R3K b - - 21 1").outcome().reason == "no progress"


def test_checkmate_beats_no_progress():
    pos = Position("k4/5/1K3/5/5/4R w - - 20 1")
    pos.push(pos.parse_uci("e1e6"))
    assert pos.clock == 21 and pos.outcome().reason == "checkmate"


# ---- notation and Game -----------------------------------------------------------

def test_san():
    pos = Position("k4/5/1K3/5/5/R3R w - - 0 1")
    assert pos.san(pos.parse_uci("a1c1")) == "Rac1"
    assert pos.san(pos.parse_uci("e1e6")) == "Re6#"
    pos = Position("k4/2P2/5/5/5/4K w - - 0 1")
    assert pos.san(pos.parse_uci("c5c6q")) == "c6=Q+"
    pos = Position("k4/5/1K3/R4/5/R4 w - - 0 1")
    assert pos.san(pos.parse_uci("a1a2")) == "R1a2+"
    assert pos.san(pos.parse_uci("a3a2")) == "R3a2+"
    pos = Position("rnknr/ppppp/5/1P3/P1PPP/RNKNR b - - 0 1")
    pos.push(pos.parse_uci("a5a3"))
    assert pos.san(pos.parse_uci("b3a4")) == "bxa4"


def test_game_play_and_undo():
    g = Game()
    assert g.play("c2c4") == "c4"
    assert g.play("b6c4") == "Nbxc4"
    g = Game()
    g.play("c2c3")
    with pytest.raises(ValueError):
        g.play("c3c5")
    g.undo()
    assert g.position.fen() == START_FEN and g.moves == []


def test_game_rejects_moves_after_end():
    g = Game(start_fen="k4/5/1K3/5/5/4R w - - 0 1")
    g.play("e1e6")
    assert g.outcome().reason == "checkmate" and g.legal_moves() == []
    with pytest.raises(ValueError):
        g.play("b4b5")


def test_promotion_to_queen_only_with_nbrq():
    g = Game(rules=NR_SINGLE, start_fen="k4/2P2/5/5/5/4K w - - 0 1")
    with pytest.raises(ValueError):
        g.play("c5c6q")
    assert g.play("c5c6r") == "c6=R+"
    assert g.position.board[parse_square("c6")] == ROOK
    assert QUEEN not in g.rules.promotions

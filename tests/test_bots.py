"""Tests every bot must pass, plus tests of the search behind the classical bot."""

import random

import pytest

from midknight.bots import BOTS, Bot, bot_list, make_bot
from midknight.bots._evaluation import FEATURES, DEFAULT_WEIGHTS, features, make_evaluator
from midknight.bots._search import MATE, MATE_BOUND, Search
from midknight.engine import Game, Position, Rules, move_to_uci

NR_SINGLE = Rules.from_dict({"promotions": "NR", "double_push": False})


def bot_params():
    for name, cls in sorted(BOTS.items()):
        yield pytest.param(name, marks=pytest.mark.slow) if cls.slow else pytest.param(name)


def play_game(white, black, rules=None, max_plies=200):
    """Play a whole game between two bots; every move must be legal."""
    g = Game(rules=rules or Rules())
    bots = {1: white, -1: black}
    while g.outcome() is None and len(g.moves) < max_plies:
        legal = g.legal_moves()
        before = g.position.fen()
        move = bots[g.position.turn].choose(g.position, legal)
        assert g.position.fen() == before, "choose() must not change the position"
        assert move in legal
        g.play(move)
    return g


# ---- registry ------------------------------------------------------------------

def test_registry_discovers_bots_and_skips_helpers():
    assert {"random", "classical"} <= set(BOTS)
    assert all(not cls.__module__.split(".")[-1].startswith("_") for cls in BOTS.values())
    names = [b["name"] for b in bot_list()]
    assert names.index("random") < names.index("classical")  # weakest first


def test_settings_are_validated():
    with pytest.raises(ValueError):
        make_bot("classical", 1, {"level": 99})
    with pytest.raises(ValueError):
        make_bot("classical", 1, {"speed": 3})
    assert make_bot("classical", 1, {"level": "2"}).settings == {"level": 2}
    assert make_bot("classical", 1).settings == {"level": 4}


# ---- tests every bot must pass ------------------------------------------------------

@pytest.mark.parametrize("name", list(bot_params()))
@pytest.mark.parametrize("rules", [Rules(), NR_SINGLE], ids=["training", "nr-single"])
def test_bot_plays_whole_games_legally(name, rules):
    for seed in range(2):
        play_game(make_bot(name, seed), make_bot("random", seed + 100), rules)
        play_game(make_bot("random", seed + 200), make_bot(name, seed), rules)


@pytest.mark.parametrize("name", list(bot_params()))
def test_seed_reproduces_game(name):
    a = play_game(make_bot(name, 7), make_bot(name, 8), max_plies=40)
    b = play_game(make_bot(name, 7), make_bot(name, 8), max_plies=40)
    assert [m for m, _ in a.moves] == [m for m, _ in b.moves]


@pytest.mark.parametrize("name", sorted(BOTS))
def test_bot_never_returns_illegal_move(name):
    rng = random.Random(3)
    bot = make_bot(name, 1, {"level": 1} if name == "classical" else {})
    for _ in range(30):
        pos = Position(rules=rng.choice([Rules(), NR_SINGLE]))
        for _ in range(rng.randint(0, 40)):
            if pos.outcome():
                break
            pos.push(rng.choice(pos.legal_moves()))
        if pos.outcome():
            continue
        legal = pos.legal_moves()
        assert bot.choose(pos, legal) in legal
        # A restricted legal list must be respected too.
        sub = legal[: max(1, len(legal) // 2)]
        assert bot.choose(pos, sub) in sub


def test_decision_depends_only_on_seed_and_position():
    pos = Position()
    bot = make_bot("random", 5)
    first = bot.choose(pos)
    for _ in range(5):
        bot.choose(Position("k4/5/5/5/5/R3K w - - 0 1"))  # other calls must not matter
        assert bot.choose(pos) == first
    assert len({make_bot("random", s).choose(pos) for s in range(30)}) > 3


def test_illegal_move_from_a_bot_is_caught():
    class Broken(Bot):
        def act(self, position, legal):
            return (0, 29, 0)

    with pytest.raises(RuntimeError):
        Broken(1).choose(Position())


def test_classical_level_1_plays_a_whole_game_quickly():
    g = play_game(make_bot("classical", 1, {"level": 1}), make_bot("random", 2))
    assert g.outcome() is not None or len(g.moves) == 200


# ---- evaluation ------------------------------------------------------------------

def test_start_position_is_balanced():
    f = features(Position())
    assert len(f) == len(FEATURES) == len(DEFAULT_WEIGHTS)
    assert all(v == 0 for name, v in zip(FEATURES, f) if name != "tempo")


def test_evaluation_is_symmetric():
    """Mirroring the board and swapping colours negates every feature."""
    rng = random.Random(11)
    for _ in range(40):
        pos = Position()
        for _ in range(rng.randint(0, 30)):
            if pos.outcome():
                break
            pos.push(rng.choice(pos.legal_moves()))
        mirrored = Position(_mirror_fen(pos.fen()))
        assert features(mirrored) == [-x for x in features(pos)]


def _mirror_fen(fen):
    placement, turn, castling, ep, clock, full = fen.split()
    rows = placement.split("/")[::-1]
    rows = ["".join(c.swapcase() for c in row) for row in rows]
    if ep != "-":
        ep = ep[0] + str(7 - int(ep[1]))
    return f"{'/'.join(rows)} {'b' if turn == 'w' else 'w'} {castling} {ep} {clock} {full}"


def test_material_matches_paper():
    ev = make_evaluator({**{k: 0 for k in DEFAULT_WEIGHTS}, "pawn": 1, "knight": 2, "rook": 4})
    assert ev(Position("k4/5/5/5/5/RN2K w - - 0 1")) == 600
    assert ev(Position("k4/5/5/5/5/RN2K b - - 0 1")) == -600


# ---- search ------------------------------------------------------------------------

def search(fen, depth=3, nodes=200_000, **kw):
    pos = Position(fen)
    s = Search(make_evaluator(), depth, nodes, **kw)
    before = pos.key()
    r = s.run(pos, pos.legal_moves())
    assert pos.key() == before, "search must leave the position unchanged"
    return r


def test_finds_mate_in_one():
    r = search("k4/5/1K3/5/5/4R w - - 0 1")
    assert move_to_uci(r.move) == "e1e6" and r.score == MATE - 1


def test_finds_mate_in_two():
    fen = "k4/5/2K2/5/5/3R1 w - - 0 1"
    r = search(fen, depth=4)
    assert r.score == MATE - 3
    pos = Position(fen)
    pos.push(r.move)
    for reply in pos.legal_moves():  # every reply allows mate in one
        pos.push(reply)
        assert search(pos.fen(), depth=2).score == MATE - 1
        pos.pop()


def test_wins_a_hanging_rook():
    r = search("k4/5/5/2r2/5/2R1K w - - 0 1", depth=2)
    assert move_to_uci(r.move) == "c1c3"


def test_does_not_hang_its_rook():
    # The white rook is attacked by a pawn; it must move away (or capture).
    r = search("k3n/5/1p3/R4/5/4K w - - 0 1", depth=3)
    assert r.move[0] == Position("k3n/5/1p3/R4/5/4K w - - 0 1").board.index(4)


def test_sees_forced_mate_against_itself():
    # Black's only move is Kb6, and then Re6 is mate.
    r = search("k4/5/1K3/5/5/4R b - - 0 1", depth=3)
    assert move_to_uci(r.move) == "a6b6" and r.score == -(MATE - 2)


def test_avoids_a_mate_in_one_when_it_can():
    # Black has a rook that can cover the back rank; doing nothing allows Re6#.
    r = search("k4/5/1K3/5/5/r3R b - - 0 1", depth=3)
    assert r.score > -MATE_BOUND


@pytest.mark.parametrize("fen", [
    "rnknr/ppppp/5/5/PPPPP/RNKNR w - - 0 1",
    "r1k1r/pp1pp/2n2/2P2/PP1PP/R1KNR b - - 0 4",
    "k4/2P2/5/5/5/4K w - - 0 1",
])
def test_move_ordering_does_not_change_the_value(fen):
    """Alpha-beta is exact: ordering and killers change speed, never the score."""
    a = search(fen, depth=3, use_tt=False, use_ordering=False)
    b = search(fen, depth=3, use_tt=False, use_ordering=True)
    assert a.score == b.score
    assert b.nodes <= a.nodes


def test_node_budget_is_deterministic():
    r1 = search(START := "rnknr/ppppp/5/5/PPPPP/RNKNR w - - 0 1", depth=8, nodes=3000)
    r2 = search(START, depth=8, nodes=3000)
    assert (r1.move, r1.score, r1.depth) == (r2.move, r2.score, r2.depth)
    assert r1.nodes <= 3001


def test_promotes_with_the_best_piece():
    r = search("4k/P4/5/5/5/K4 w - - 0 1", depth=2)
    assert move_to_uci(r.move) == "a5a6q"
    r = Search(make_evaluator(), 2, 10_000).run(
        p := Position("4k/P4/5/5/5/K4 w - - 0 1", NR_SINGLE), p.legal_moves())
    assert move_to_uci(r.move) == "a5a6r"

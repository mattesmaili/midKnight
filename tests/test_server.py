"""Server tests: a real server on a free port, driven over HTTP."""

import json
import random
import threading
import urllib.error
import urllib.request

import pytest

from midknight.engine import Game, move_to_uci
from midknight.server import make_server


@pytest.fixture(scope="module")
def base_url():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def call(base_url, path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base_url + path, data=data, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_index_and_pieces_are_served(base_url):
    with urllib.request.urlopen(base_url + "/") as r:
        assert r.status == 200 and b"MidKnight" in r.read()
    with urllib.request.urlopen(base_url + "/pieces/wN.svg") as r:
        assert r.headers["Content-Type"].startswith("image/svg+xml")
    for path, ctype in [("/sounds/move.mp3", "audio/mpeg"), ("/fonts/Fredoka.woff2", "font/woff2"),
                        ("/app.js", "text/javascript")]:
        with urllib.request.urlopen(base_url + path) as r:
            assert r.headers["Content-Type"].startswith(ctype), path


def test_static_path_traversal_blocked(base_url):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base_url + "/../midknight/engine.py")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base_url + "/%2e%2e/run.py")


def test_options(base_url):
    status, data = call(base_url, "/api/options")
    assert status == 200
    assert data["rules"]["default"] == {"promotions": "NBRQ", "double_push": True}
    assert data["start_board"][2] == "wK" and data["start_board"][27] == "bK" and data["start_board"][12] is None


def test_new_game_move_undo(base_url):
    status, s = call(base_url, "/api/games", {})
    assert status == 201
    assert s["turn"] == "w" and len(s["legal"]) == 14 and s["board"][2] == "wK" and s["board"][27] == "bK"
    gid = s["id"]
    status, s = call(base_url, f"/api/games/{gid}/move", {"move": "c2c4"})
    assert status == 200 and s["moves"] == ["c4"] and s["last_move"] == {"from": "c2", "to": "c4"}
    assert s["turn"] == "b" and s["fen"].split()[3] == "c3"
    status, s = call(base_url, f"/api/games/{gid}/undo", {})
    assert status == 200 and s["moves"] == [] and s["last_move"] is None
    status, s = call(base_url, f"/api/games/{gid}")
    assert status == 200 and s["fen"].startswith("rnknr/ppppp/5/5/PPPPP/RNKNR w")


def test_rule_options_change_the_game(base_url):
    _, s = call(base_url, "/api/games", {"rules": {"promotions": "NR", "double_push": False}})
    assert s["rules"] == {"promotions": "NR", "double_push": False}
    assert len(s["legal"]) == 9


def test_errors(base_url):
    assert call(base_url, "/api/games", {"rules": {"promotions": "Q"}})[0] == 400
    assert call(base_url, "/api/games/nope")[0] == 404
    _, s = call(base_url, "/api/games", {})
    assert call(base_url, f"/api/games/{s['id']}/move", {"move": "c2c5"})[0] == 400
    assert call(base_url, f"/api/games/{s['id']}/move", {"mv": 1})[0] == 400
    assert call(base_url, f"/api/games/{s['id']}/undo", {})[0] == 400
    assert call(base_url, "/api/unknown")[0] == 404


def _random_mating_game():
    rng = random.Random(1)
    while True:
        g = Game()
        while g.outcome() is None:
            g.play(rng.choice(g.legal_moves()))
        if g.outcome().reason == "checkmate":
            return g


def test_checkmate_is_reported(base_url):
    game = _random_mating_game()
    _, s = call(base_url, "/api/games", {})
    for move, _ in game.moves:
        status, s = call(base_url, f"/api/games/{s['id']}/move", {"move": move_to_uci(move)})
        assert status == 200
    assert s["outcome"] == {"result": game.outcome().result, "reason": "checkmate"}
    assert s["legal"] == [] and s["check"] is not None
    assert s["moves"][-1].endswith("#")


# ---- games against a bot ------------------------------------------------------------

def new_bot_game(base_url, color="w", seed=42, bot="random", settings=None):
    status, s = call(base_url, "/api/games", {"mode": "bot", "color": color, "seed": seed,
                                               "bot": {"name": bot, "settings": settings or {}}})
    assert status == 201, s
    return s


def test_options_list_bots(base_url):
    _, data = call(base_url, "/api/options")
    names = [b["name"] for b in data["bots"]]
    assert "random" in names and "classical" in names
    classical = next(b for b in data["bots"] if b["name"] == "classical")
    assert classical["options"][0]["key"] == "level"


def test_play_against_a_bot(base_url):
    s = new_bot_game(base_url, "w")
    assert s["players"]["b"]["type"] == "bot" and s["players"]["w"]["type"] == "human"
    assert not s["bot_to_move"] and s["legal"] and not s["can_undo"]
    gid = s["id"]
    _, s = call(base_url, f"/api/games/{gid}/move", {"move": "c2c3"})
    assert s["bot_to_move"] and s["legal"] == []
    assert call(base_url, f"/api/games/{gid}/move", {"move": "d2d3"})[0] == 409
    status, s = call(base_url, f"/api/games/{gid}/bot", {})
    assert status == 200 and len(s["moves"]) == 2 and not s["bot_to_move"] and s["legal"]
    assert call(base_url, f"/api/games/{gid}/bot", {})[0] == 409
    # Undo takes back the bot's reply and your move.
    status, s = call(base_url, f"/api/games/{gid}/undo", {})
    assert status == 200 and s["moves"] == [] and not s["can_undo"]


def test_bot_moves_first_when_you_are_black(base_url):
    s = new_bot_game(base_url, "b", bot="classical", settings={"level": 1})
    assert s["players"]["w"]["type"] == "bot" and s["bot_to_move"] and not s["can_undo"]
    _, s = call(base_url, f"/api/games/{s['id']}/bot", {})
    assert len(s["moves"]) == 1 and s["turn"] == "b" and s["legal"]
    assert "depth" in s["bot_info"]
    assert call(base_url, f"/api/games/{s['id']}/undo", {})[0] == 400


def test_seed_replays_the_bots_moves(base_url):
    def play(seed):
        s = new_bot_game(base_url, "random", seed=seed, bot="classical", settings={"level": 2})
        gid = s["id"]
        for _ in range(6):
            if s["outcome"]:
                break
            if s["bot_to_move"]:
                _, s = call(base_url, f"/api/games/{gid}/bot", {})
            else:
                _, s = call(base_url, f"/api/games/{gid}/move", {"move": sorted(s["legal"])[0]})
        return s["players"], s["moves"]

    assert play(1234) == play(1234)


def test_undo_then_replay_gives_the_same_bot_move(base_url):
    s = new_bot_game(base_url, "w", seed=9, bot="random")
    gid = s["id"]
    call(base_url, f"/api/games/{gid}/move", {"move": "b2b3"})
    _, first = call(base_url, f"/api/games/{gid}/bot", {})
    call(base_url, f"/api/games/{gid}/undo", {})
    call(base_url, f"/api/games/{gid}/move", {"move": "b2b3"})
    _, again = call(base_url, f"/api/games/{gid}/bot", {})
    assert first["moves"] == again["moves"]


def test_bad_bot_requests(base_url):
    assert call(base_url, "/api/games", {"mode": "bot", "bot": {"name": "nope"}})[0] == 400
    assert call(base_url, "/api/games", {"mode": "bot", "bot": {"name": "classical", "settings": {"level": 0}}})[0] == 400
    assert call(base_url, "/api/games", {"mode": "bot", "color": "x", "bot": {"name": "random"}})[0] == 400
    assert call(base_url, "/api/games", {"mode": "chess960"})[0] == 400
    assert call(base_url, "/api/games", {"seed": "abc"})[0] == 400
    _, s = call(base_url, "/api/games", {})
    assert call(base_url, f"/api/games/{s['id']}/bot", {})[0] == 409  # no bot in a 1v1 game

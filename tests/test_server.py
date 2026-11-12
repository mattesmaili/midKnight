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

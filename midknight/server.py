"""A small HTTP server (standard library only) that holds games and serves the page.

JSON API:

    GET  /api/options                 rule options for the New game dialog
    POST /api/games                   {"rules": {"promotions": "NBRQ"|"NR", "double_push": bool}}
    GET  /api/games/<id>              current state
    POST /api/games/<id>/move         {"move": "c2c3"}   (UCI; promotion as a 5th letter)
    POST /api/games/<id>/undo         take back the last move

Every endpoint that touches a game returns its full state (see ``game_state``).
The page never applies rules itself: it draws the state and posts moves.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import (
    PIECE_LETTERS, Game, Position, Rules, move_to_uci, square_name,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
CONTENT_TYPES = {".woff2": "font/woff2", ".mp3": "audio/mpeg", ".svg": "image/svg+xml", ".js": "text/javascript"}
MAX_GAMES = 500


def board_codes(pos: Position) -> list:
    """Board as piece codes ("wK", "bP", None), index = rank * 5 + file, a1 = 0."""
    return [None if p == 0 else ("w" if p > 0 else "b") + PIECE_LETTERS[abs(p)] for p in pos.board]


def game_state(game_id: str, game: Game) -> dict:
    pos = game.position
    board = board_codes(pos)
    outcome = game.outcome()
    check = square_name(pos.kings[pos.turn]) if pos.in_check() else None
    last = None
    if game.moves:
        frm, to, _ = game.moves[-1][0]
        last = {"from": square_name(frm), "to": square_name(to)}
    return {
        "id": game_id,
        "rules": game.rules.to_dict(),
        "fen": pos.fen(),
        "board": board,
        "turn": "w" if pos.turn > 0 else "b",
        "legal": [move_to_uci(m) for m in game.legal_moves()],
        "last_move": last,
        "check": check,
        "moves": [san for _, san in game.moves],
        "outcome": outcome._asdict() if outcome else None,
        "clock": pos.clock,
    }


class GameStore:
    """Games in memory, oldest dropped first once there are too many."""

    def __init__(self, limit: int = MAX_GAMES):
        self.limit = limit
        self.games: OrderedDict[str, Game] = OrderedDict()
        self.lock = threading.Lock()

    def create(self, rules: Rules) -> tuple:
        game_id = secrets.token_hex(6)
        game = Game(rules=rules)
        with self.lock:
            self.games[game_id] = game
            while len(self.games) > self.limit:
                self.games.popitem(last=False)
        return game_id, game

    def get(self, game_id: str) -> Game | None:
        with self.lock:
            game = self.games.get(game_id)
            if game is not None:
                self.games.move_to_end(game_id)
            return game


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def options() -> dict:
    return {
        "start_board": board_codes(Position()),
        "rules": {
            "promotions": [
                {"value": "NBRQ", "label": "Knight, bishop, rook or queen",
                 "description": "The rules the ELOquent network was trained on."},
                {"value": "NR", "label": "Knight or rook only",
                 "description": "No new piece types appear on the board."},
            ],
            "double_push": [
                {"value": True, "label": "Two squares, with en passant",
                 "description": "A pawn's first move may go two squares (training rules)."},
                {"value": False, "label": "One square only",
                 "description": "Pawns always move one square; no en passant."},
            ],
            "default": Rules().to_dict(),
        },
    }


class Handler(BaseHTTPRequestHandler):
    store: GameStore  # set by make_server
    server_version = "MidKnight/0.1"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ---- plumbing ----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 10_000:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
        return data

    def _game(self, game_id: str) -> Game:
        game = self.store.get(game_id)
        if game is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such game")
        return game

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                self._api(method, [p for p in path[5:].split("/") if p])
            elif method in ("GET", "HEAD"):
                self._static(path)
            else:
                raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        except ApiError as e:
            self._json(e.status, {"error": e.message})

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    # ---- routes ----------------------------------------------------------------

    def _api(self, method: str, parts: list) -> None:
        if parts == ["options"] and method == "GET":
            return self._json(HTTPStatus.OK, options())
        if parts == ["games"] and method == "POST":
            body = self._body()
            try:
                rules = Rules.from_dict(body.get("rules"))
            except ValueError as e:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
            game_id, game = self.store.create(rules)
            return self._json(HTTPStatus.CREATED, game_state(game_id, game))
        if len(parts) >= 2 and parts[0] == "games":
            game_id, game = parts[1], self._game(parts[1])
            action = parts[2:]
            if action == [] and method == "GET":
                with self.store.lock:
                    return self._json(HTTPStatus.OK, game_state(game_id, game))
            if action == ["move"] and method == "POST":
                move = self._body().get("move")
                if not isinstance(move, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, 'expected {"move": "c2c3"}')
                with self.store.lock:
                    try:
                        game.play(move)
                    except ValueError as e:
                        raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
                    return self._json(HTTPStatus.OK, game_state(game_id, game))
            if action == ["undo"] and method == "POST":
                with self.store.lock:
                    if game.undo() is None:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "nothing to undo")
                    return self._json(HTTPStatus.OK, game_state(game_id, game))
        raise ApiError(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR not in target.parents or not target.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        ctype = (CONTENT_TYPES.get(target.suffix.lower())
                 or mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        if ctype.startswith("text/") or ctype == "image/svg+xml":
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)


def make_server(host: str = "127.0.0.1", port: int = 8234) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"store": GameStore()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server

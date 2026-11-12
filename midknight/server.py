"""A small HTTP server (standard library only) that holds games and serves the page.

JSON API:

    GET  /api/options                 rule options and bots for the New game dialog
    POST /api/games                   {"rules": {"promotions": "NBRQ"|"NR", "double_push": bool},
                                       "mode": "1v1"|"bot", "bot": {"name": ..., "settings": {...}},
                                       "color": "w"|"b"|"random", "seed": int (optional)}
    GET  /api/games/<id>              current state
    POST /api/games/<id>/move         {"move": "c2c3"}   (UCI; promotion as a 5th letter)
    POST /api/games/<id>/bot          the bot to move plays its move
    POST /api/games/<id>/undo         take back the last move (vs a bot: back to before your last move)

A game's seed fixes every random choice: the colour when "random" is asked
for, and every bot decision (a bot's move depends only on the seed, its
settings and the position). Replaying the same moves with the same seed
replays the game exactly.

Every endpoint that touches a game returns its full state (see ``game_state``).
The page never applies rules itself: it draws the state and posts moves.
"""

from __future__ import annotations

import json
import mimetypes
import random
import secrets
import threading
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .bots import Bot, bot_list, get_bot, make_bot
from .engine import (
    BLACK, PIECE_LETTERS, WHITE, Game, Position, Rules, move_to_uci, square_name,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
CONTENT_TYPES = {".woff2": "font/woff2", ".mp3": "audio/mpeg", ".svg": "image/svg+xml", ".js": "text/javascript"}
MAX_GAMES = 500


def board_codes(pos: Position) -> list:
    """Board as piece codes ("wK", "bP", None), index = rank * 5 + file, a1 = 0."""
    return [None if p == 0 else ("w" if p > 0 else "b") + PIECE_LETTERS[abs(p)] for p in pos.board]


class Session:
    """A game plus who plays each side and the seed."""

    def __init__(self, game: Game, seed: int, players: dict):
        self.game = game
        self.seed = seed
        self.players = players  # {WHITE: Bot | None, BLACK: Bot | None}; None is a human

    def bot_to_move(self) -> Bot | None:
        return self.players[self.game.position.turn] if self.game.outcome() is None else None

    def human_moved(self) -> bool:
        start = self.game.position.ply - len(self.game.moves)
        return any(self.players[WHITE if (start + i) % 2 == 0 else BLACK] is None
                   for i in range(len(self.game.moves)))

    def undo(self) -> bool:
        """Take back moves until it is a human's turn again, removing one human move."""
        if not self.human_moved():
            return False
        while self.game.moves:
            self.game.undo()
            if self.players[self.game.position.turn] is None:
                break
        return True


def player_info(bot: Bot | None) -> dict:
    if bot is None:
        return {"type": "human"}
    return {"type": "bot", "name": bot.name, "label": bot.label, "settings": bot.settings}


def game_state(game_id: str, session: Session) -> dict:
    game = session.game
    pos = game.position
    outcome = game.outcome()
    check = square_name(pos.kings[pos.turn]) if pos.in_check() else None
    last = None
    if game.moves:
        frm, to, _ = game.moves[-1][0]
        last = {"from": square_name(frm), "to": square_name(to)}
    bot = session.bot_to_move()
    return {
        "id": game_id,
        "seed": session.seed,
        "players": {"w": player_info(session.players[WHITE]), "b": player_info(session.players[BLACK])},
        "rules": game.rules.to_dict(),
        "fen": pos.fen(),
        "board": board_codes(pos),
        "turn": "w" if pos.turn > 0 else "b",
        # Legal moves are only offered to a human whose turn it is.
        "legal": [] if bot else [move_to_uci(m) for m in game.legal_moves()],
        "bot_to_move": bot is not None,
        "can_undo": session.human_moved(),
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
        self.games: OrderedDict[str, Session] = OrderedDict()
        self.lock = threading.Lock()

    def create(self, session: Session) -> str:
        game_id = secrets.token_hex(6)
        with self.lock:
            self.games[game_id] = session
            while len(self.games) > self.limit:
                self.games.popitem(last=False)
        return game_id

    def get(self, game_id: str) -> Session | None:
        with self.lock:
            session = self.games.get(game_id)
            if session is not None:
                self.games.move_to_end(game_id)
            return session


def new_session(body: dict) -> Session:
    """Build a game from a POST /api/games body. Raises ValueError on bad input."""
    rules = Rules.from_dict(body.get("rules"))
    seed = body.get("seed")
    if seed in (None, ""):
        seed = secrets.randbelow(1_000_000)
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise ValueError("seed must be an integer") from None
    if not 0 <= seed < 2 ** 63:
        raise ValueError("seed must be a non-negative integer")
    mode = body.get("mode", "1v1")
    players = {WHITE: None, BLACK: None}
    if mode == "bot":
        spec = body.get("bot") or {}
        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
            raise ValueError('expected "bot": {"name": ..., "settings": {...}}')
        settings = spec.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError("bot settings must be an object")
        color = body.get("color", "w")
        if color == "random":
            color = random.Random(f"{seed}:color").choice("wb")
        if color not in ("w", "b"):
            raise ValueError('color must be "w", "b" or "random"')
        bot_side = BLACK if color == "w" else WHITE
        get_bot(spec["name"])  # unknown names raise ValueError
        players[bot_side] = make_bot(spec["name"], f"{seed}:{'w' if bot_side == WHITE else 'b'}", settings)
    elif mode != "1v1":
        raise ValueError('mode must be "1v1" or "bot"')
    return Session(Game(rules=rules), seed, players)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def options() -> dict:
    return {
        "start_board": board_codes(Position()),
        "bots": bot_list(),
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

    def _session(self, game_id: str) -> Session:
        session = self.store.get(game_id)
        if session is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such game")
        return session

    def _bot_move(self, game_id: str, session: Session) -> None:
        game = session.game
        with self.store.lock:
            bot = session.bot_to_move()
            if bot is None:
                raise ApiError(HTTPStatus.CONFLICT, "it is not the computer's turn")
            position = game.position.copy()
            n_moves = len(game.moves)
        move = bot.choose(position)  # may take a few seconds; runs outside the lock
        info = dict(bot.info)
        with self.store.lock:
            if len(game.moves) != n_moves or session.bot_to_move() is not bot:
                raise ApiError(HTTPStatus.CONFLICT, "the game changed while the computer was thinking")
            game.play(move)
            state = game_state(game_id, session)
        state["bot_info"] = {k: (v if k != "pv" else [move_to_uci(m) for m in v]) for k, v in info.items()}
        return self._json(HTTPStatus.OK, state)

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
            try:
                session = new_session(self._body())
            except ValueError as e:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
            game_id = self.store.create(session)
            with self.store.lock:
                return self._json(HTTPStatus.CREATED, game_state(game_id, session))
        if len(parts) >= 2 and parts[0] == "games":
            game_id, session = parts[1], self._session(parts[1])
            game = session.game
            action = parts[2:]
            if action == [] and method == "GET":
                with self.store.lock:
                    return self._json(HTTPStatus.OK, game_state(game_id, session))
            if action == ["move"] and method == "POST":
                move = self._body().get("move")
                if not isinstance(move, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, 'expected {"move": "c2c3"}')
                with self.store.lock:
                    if session.bot_to_move():
                        raise ApiError(HTTPStatus.CONFLICT, "it is the computer's turn")
                    try:
                        game.play(move)
                    except ValueError as e:
                        raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
                    return self._json(HTTPStatus.OK, game_state(game_id, session))
            if action == ["bot"] and method == "POST":
                return self._bot_move(game_id, session)
            if action == ["undo"] and method == "POST":
                with self.store.lock:
                    if not session.undo():
                        raise ApiError(HTTPStatus.BAD_REQUEST, "nothing to undo")
                    return self._json(HTTPStatus.OK, game_state(game_id, session))
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

"""The classical engine from the paper's section 2: alpha-beta search over a
hand-weighted evaluation (material, king safety and activity, mobility and
synergy, passed pawns)."""

from __future__ import annotations

import zlib

from ._evaluation import make_evaluator
from ._search import Search, zobrist
from .base import Bot, IntOption

# level -> (max depth, node budget, evaluation noise in centipawns)
LEVELS = {
    1: (1, 2_000, 150),
    2: (1, 2_000, 60),
    3: (2, 10_000, 30),
    4: (3, 20_000, 0),
    5: (4, 40_000, 0),
    6: (5, 80_000, 0),
    7: (6, 150_000, 0),
    8: (8, 300_000, 0),
}
LEVEL_NAMES = {1: "Beginner", 2: "Novice", 3: "Casual", 4: "Club", 5: "Strong", 6: "Expert", 7: "Master", 8: "Maximum"}


class ClassicalBot(Bot):
    name = "classical"
    label = "Classical engine"
    description = ("Alpha-beta search with iterative deepening, quiescence search and a transposition "
                   "table, over a hand-weighted evaluation (paper, section 2).")
    strength = 10
    slow = True
    options = (
        IntOption("level", "Level", 4, 1, 8,
                  "Low levels search shallowly and misjudge positions on purpose; "
                  "level 8 searches up to 8 plies (a few seconds a move).",
                  labels=LEVEL_NAMES),
    )

    weights = None  # subclasses may plug in learned weights

    def setup(self):
        self.depth, self.nodes, self.noise = LEVELS[self.settings["level"]]
        self.base_eval = make_evaluator(self.weights)

    def _evaluator(self):
        if not self.noise:
            return self.base_eval
        # Deterministic per (seed, position): the same position is always misjudged the same way.
        salt = zlib.crc32(str(self.seed).encode())
        span = 2 * self.noise + 1
        base = self.base_eval

        def noisy(pos):
            h = (zobrist(pos) ^ salt) * 0x9E3779B97F4A7C15 & 0xFFFFFFFFFFFFFFFF
            return base(pos) + (h >> 40) % span - self.noise

        return noisy

    def act(self, position, legal):
        search = Search(self._evaluator(), self.depth, self.nodes)
        result = search.run(position, legal, self.rng)
        self.info = {"score": result.score, "depth": result.depth, "nodes": result.nodes,
                     "pv": result.pv}
        return result.move

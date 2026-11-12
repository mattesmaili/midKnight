"""The Bot base class and the settings a bot can declare.

A bot is a class with a registry ``name``, a ``label`` and ``description`` for
the New game dialog, a ``strength`` used to order the list (weakest first),
and optional ``options`` (settings shown under the bot picker).

``choose(position, legal)`` is the entry point. It seeds ``self.rng`` from the
game seed and the position, then calls ``act``. A bot's choice is therefore a
pure function of (seed, settings, position): replaying a game with the same
seed reproduces every bot move, and undo cannot desynchronise anything.
Search budgets must count nodes or iterations, never seconds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..engine import Move, Position


@dataclass(frozen=True)
class IntOption:
    """An integer setting in [low, high]."""

    key: str
    label: str
    default: int
    low: int
    high: int
    description: str = ""
    labels: dict = field(default_factory=dict)  # optional names for some values

    def validate(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{self.key} must be an integer")
        try:
            v = int(value)
        except ValueError:
            raise ValueError(f"{self.key} must be an integer") from None
        if not self.low <= v <= self.high:
            raise ValueError(f"{self.key} must be between {self.low} and {self.high}")
        return v

    def to_dict(self) -> dict:
        return {"type": "int", "key": self.key, "label": self.label, "default": self.default,
                "low": self.low, "high": self.high, "description": self.description,
                "labels": {str(k): v for k, v in self.labels.items()}}


@dataclass(frozen=True)
class ChoiceOption:
    """One value from a fixed list of (value, label) pairs."""

    key: str
    label: str
    default: Any
    choices: tuple
    description: str = ""

    def validate(self, value: Any) -> Any:
        for v, _ in self.choices:
            if v == value or str(v) == str(value):
                return v
        raise ValueError(f"{self.key} must be one of {[v for v, _ in self.choices]}")

    def to_dict(self) -> dict:
        return {"type": "choice", "key": self.key, "label": self.label, "default": self.default,
                "choices": [{"value": v, "label": lab} for v, lab in self.choices],
                "description": self.description}


class Bot:
    """Base class: subclass it in a module in ``midknight/bots/``."""

    name: ClassVar[str] = ""          # registry key; empty means "abstract, don't list"
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    strength: ClassVar[int] = 0       # sort key, weakest first
    slow: ClassVar[bool] = False      # whole-game tests only with pytest -m slow
    options: ClassVar[tuple] = ()

    def __init__(self, seed: int | str = 0, **settings: Any):
        known = {o.key: o for o in self.options}
        unknown = set(settings) - set(known)
        if unknown:
            raise ValueError(f"{self.name} has no setting(s) {sorted(unknown)}")
        self.settings = {k: o.validate(settings.get(k, o.default)) for k, o in known.items()}
        self.seed = seed
        self.rng = random.Random(f"{seed}")
        self.info: dict = {}              # optional details about the last decision
        self.setup()

    def setup(self) -> None:
        """Hook for subclasses: read ``self.settings`` once."""

    def choose(self, position: Position, legal: list | None = None) -> Move:
        """Pick a move. ``position`` is left exactly as it was."""
        pos = position.copy()
        legal = pos.legal_moves() if legal is None else list(legal)
        if not legal:
            raise ValueError("no legal moves")
        self.rng = random.Random(f"{self.seed}|{pos.fen()}")
        self.info = {}
        move = tuple(self.act(pos, legal))
        if move not in legal:
            raise RuntimeError(f"{self.name} returned an illegal move {move}")
        return move

    def act(self, position: Position, legal: list) -> Move:
        """Return one of ``legal``. ``position`` is a private copy; push/pop freely."""
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "label": cls.label, "description": cls.description,
                "strength": cls.strength, "options": [o.to_dict() for o in cls.options]}

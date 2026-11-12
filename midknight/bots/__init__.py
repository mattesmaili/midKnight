"""Bots, discovered automatically.

Every ``Bot`` subclass with a ``name`` in a module of this package is
registered. Modules whose names start with ``_`` are helpers and are skipped,
as is ``base``. A module that fails to import (for example because an
optional dependency such as TensorFlow is missing) is left out and its error
is kept in ``UNAVAILABLE`` so the game still starts.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import Bot, ChoiceOption, IntOption

__all__ = ["Bot", "ChoiceOption", "IntOption", "BOTS", "UNAVAILABLE", "get_bot", "make_bot", "bot_list"]

BOTS: dict = {}
UNAVAILABLE: dict = {}


def _discover() -> None:
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # optional dependency missing, etc.
            UNAVAILABLE[info.name] = f"{type(e).__name__}: {e}"
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Bot) and obj is not Bot and obj.name and obj.__module__ == module.__name__:
                if obj.name in BOTS and BOTS[obj.name] is not obj:
                    raise RuntimeError(f"two bots are named {obj.name!r}")
                BOTS[obj.name] = obj


def get_bot(name: str) -> type:
    try:
        return BOTS[name]
    except KeyError:
        raise ValueError(f"unknown bot {name!r}") from None


def make_bot(name: str, seed, settings: dict | None = None) -> Bot:
    return get_bot(name)(seed=seed, **(settings or {}))


def bot_list() -> list:
    return [cls.describe() for cls in sorted(BOTS.values(), key=lambda c: (c.strength, c.name))]


_discover()

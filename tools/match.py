#!/usr/bin/env python3
"""Play two bots against each other: paired games on all CPU cores.

Each pair starts from the same random opening (a few random plies, fixed by
the seed) and is played twice with colours swapped. Reports A's wins, losses
and draws, A's score, and its standard error. Differences under about two
standard errors are noise.

    python tools/match.py random classical:level=1 --pairs 50
    python tools/match.py classical:level=3 classical:level=4 --pairs 100 --opening-plies 4
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midknight.bots import make_bot  # noqa: E402
from midknight.engine import Game, Rules  # noqa: E402


def parse_spec(spec: str):
    """'classical:level=3' -> ('classical', {'level': '3'})"""
    name, _, rest = spec.partition(":")
    settings = dict(kv.split("=", 1) for kv in rest.split(",") if kv)
    return name, settings


def play_pair(args):
    spec_a, spec_b, pair, seed, opening_plies, rules_dict, max_plies = args
    rules = Rules.from_dict(rules_dict)
    rng = random.Random(f"{seed}:{pair}")
    opening = Game(rules=rules)
    for _ in range(opening_plies):
        legal = opening.legal_moves()
        if not legal:
            break
        opening.play(rng.choice(legal))
    start_moves = [m for m, _ in opening.moves]
    results = []
    for a_is_white in (True, False):
        g = Game(rules=rules)
        for m in start_moves:
            g.play(m)
        a = make_bot(spec_a[0], f"{seed}:{pair}:a", spec_a[1])
        b = make_bot(spec_b[0], f"{seed}:{pair}:b", spec_b[1])
        players = {1: a, -1: b} if a_is_white else {1: b, -1: a}
        while g.outcome() is None and len(g.moves) < max_plies:
            g.play(players[g.position.turn].choose(g.position, g.legal_moves()))
        out = g.outcome()
        if out is None or out.winner is None:
            results.append(0.5)
        else:
            results.append(1.0 if (out.winner == 1) == a_is_white else 0.0)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--pairs", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--opening-plies", type=int, default=4)
    ap.add_argument("--promotions", default="NBRQ")
    ap.add_argument("--single-push", action="store_true")
    ap.add_argument("--max-plies", type=int, default=300)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    rules = {"promotions": args.promotions, "double_push": not args.single_push}
    jobs = [(parse_spec(args.a), parse_spec(args.b), i, args.seed, args.opening_plies, rules, args.max_plies)
            for i in range(args.pairs)]
    scores = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(play_pair, jobs):
            scores.extend(res)
    n = len(scores)
    w, d = scores.count(1.0), scores.count(0.5)
    l = n - w - d
    mean = sum(scores) / n
    # Standard error over pairs (the two games of a pair share an opening).
    pair_means = [(scores[i] + scores[i + 1]) / 2 for i in range(0, n, 2)]
    var = sum((x - mean) ** 2 for x in pair_means) / max(1, len(pair_means) - 1)
    se = math.sqrt(var / len(pair_means))
    elo = "" if mean in (0, 1) else f"  (~{-400 * math.log10(1 / mean - 1):+.0f} Elo)"
    print(f"{args.a} vs {args.b}: +{w} -{l} ={d} of {n}  score {mean:.3f} ± {se:.3f}{elo}")


if __name__ == "__main__":
    main()

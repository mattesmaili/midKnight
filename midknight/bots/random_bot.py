"""The weakest bot: any legal move."""

from .base import Bot


class RandomBot(Bot):
    name = "random"
    label = "Random"
    description = "Plays any legal move, uniformly at random."
    strength = 0

    def act(self, position, legal):
        return self.rng.choice(legal)

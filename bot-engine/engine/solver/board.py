"""Modèle de plateau : stockage, gravité, voisinage.

Les cellules vivent dans une liste Python plate : sur des grilles de 100 cases
environ, c'est plus rapide qu'un tableau NumPy (pas de surcoût d'indexation) et
cela reste lisible. NumPy est réservé à la vision.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Iterator

from ..model import BLOCK, CLEARED, HOLE, UNKNOWN, color_of, is_piece, is_special

#: Décalage maximal (en cases) appliqué lors d'un échange.
SWAP_DELTAS = ((0, 1), (1, 0))


class Board:
    __slots__ = ("rows", "cols", "cells", "static", "palette_size")

    def __init__(
        self,
        rows: int,
        cols: int,
        cells: list[int],
        static: list[bool] | None = None,
        palette_size: int = 0,
    ) -> None:
        self.rows = int(rows)
        self.cols = int(cols)
        self.cells = list(cells)
        self.static = list(static) if static is not None else [False] * self.rows * self.cols
        self.palette_size = int(palette_size)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_matrix(cls, matrix: list[list[int]], palette_size: int = 0) -> "Board":
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        cells = [value for row in matrix for value in row]
        static = [value == HOLE for value in cells]
        return cls(rows, cols, cells, static, palette_size)

    def clone(self) -> "Board":
        return Board(self.rows, self.cols, list(self.cells), list(self.static), self.palette_size)

    # ------------------------------------------------------------------ #
    def index(self, row: int, col: int) -> int:
        return row * self.cols + col

    def row_of(self, index: int) -> int:
        return index // self.cols

    def col_of(self, index: int) -> int:
        return index % self.cols

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.rows and 0 <= col < self.cols

    def is_static(self, index: int) -> bool:
        return self.static[index]

    def is_solid(self, index: int) -> bool:
        """Case qui ne tombe pas : hors plateau ou obstacle intact."""
        return self.static[index] or self.cells[index] == BLOCK

    def is_playable(self, index: int) -> bool:
        return not self.static[index] and is_piece(self.cells[index])

    def is_swappable(self, index: int) -> bool:
        return not self.static[index] and self.cells[index] >= 0

    def neighbors(self, index: int) -> Iterator[int]:
        row, col = divmod(index, self.cols)
        if row > 0:
            yield index - self.cols
        if row + 1 < self.rows:
            yield index + self.cols
        if col > 0:
            yield index - 1
        if col + 1 < self.cols:
            yield index + 1

    # ------------------------------------------------------------------ #
    def color_counts(self) -> Counter:
        counts: Counter = Counter()
        for value in self.cells:
            if value >= 0:
                color = color_of(value)
                if color < self.palette_size:
                    counts[color] += 1
        return counts

    def dominant_color(self) -> int:
        counts = self.color_counts()
        if not counts:
            return -1
        return int(counts.most_common(1)[0][0])

    def playable_indexes(self) -> list[int]:
        return [index for index in range(len(self.cells)) if self.is_playable(index)]

    def as_matrix(self) -> list[list[int]]:
        return [
            self.cells[row * self.cols : (row + 1) * self.cols] for row in range(self.rows)
        ]

    def signature(self) -> tuple:
        return tuple(self.cells)

    def render(self) -> str:
        """Représentation texte compacte (journaux et tests)."""
        lines = []
        for row in range(self.rows):
            symbols = []
            for col in range(self.cols):
                value = self.cells[self.index(row, col)]
                if value == HOLE:
                    symbols.append("  .")
                elif value == BLOCK:
                    symbols.append("  #")
                elif value == UNKNOWN:
                    symbols.append("  ?")
                elif value == CLEARED:
                    symbols.append("  _")
                elif is_special(value):
                    symbols.append(f"{color_of(value)}*{value % 8}")
                else:
                    symbols.append(f"  {value}")
            lines.append(" ".join(symbols))
        return "\n".join(lines)


# ---------------------------------------------------------------------- #
# Gravité
# ---------------------------------------------------------------------- #
def apply_gravity(board: Board, rng: random.Random | None) -> int:
    """Fait tomber les pièces et remplit le haut des colonnes.

    Les colonnes sont découpées en segments séparés par les cases solides : une
    pièce ne traverse jamais un trou du plateau ni une caisse intacte. Retourne le
    nombre de cases remplies.
    """
    filled = 0
    for col in range(board.cols):
        segment_bottom = board.rows - 1
        while segment_bottom >= 0:
            if board.is_solid(board.index(segment_bottom, col)):
                segment_bottom -= 1
                continue
            segment_top = segment_bottom
            while segment_top - 1 >= 0 and not board.is_solid(
                board.index(segment_top - 1, col)
            ):
                segment_top -= 1

            write = segment_bottom
            for row in range(segment_bottom, segment_top - 1, -1):
                index = board.index(row, col)
                value = board.cells[index]
                if value == CLEARED:
                    continue
                target = board.index(write, col)
                if target != index:
                    board.cells[target] = value
                    board.cells[index] = CLEARED
                write -= 1

            for row in range(write, segment_top - 1, -1):
                index = board.index(row, col)
                if rng is not None and board.palette_size > 0:
                    board.cells[index] = rng.randrange(board.palette_size)
                else:
                    board.cells[index] = UNKNOWN
                filled += 1

            segment_bottom = segment_top - 1
    return filled

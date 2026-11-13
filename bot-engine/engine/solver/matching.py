"""Détection des alignements et génération des échanges légaux."""

from __future__ import annotations

from dataclasses import dataclass

from ..model import WILDCARD_COLOR, color_of, is_special
from .board import Board


@dataclass
class Run:
    """Alignement de trois pièces ou plus."""

    cells: list[int]
    color: int
    horizontal: bool

    @property
    def length(self) -> int:
        return len(self.cells)

    @property
    def anchor(self) -> int:
        """Case de création du bonus : centre de l'alignement."""
        return self.cells[self.length // 2]


def _matches_at(board: Board, index: int, color: int) -> bool:
    value = board.cells[index]
    if value < 0:
        return False
    if is_special(value) and color_of(value) == WILDCARD_COLOR:
        return False
    return color_of(value) == color


def find_runs(board: Board) -> list[Run]:
    """Retourne tous les alignements horizontaux et verticaux de 3 pièces ou plus."""
    runs: list[Run] = []
    cells = board.cells
    rows, cols = board.rows, board.cols

    for row in range(rows):
        base = row * cols
        col = 0
        while col < cols:
            index = base + col
            value = cells[index]
            if value < 0 or (is_special(value) and color_of(value) == WILDCARD_COLOR):
                col += 1
                continue
            color = color_of(value)
            end = col + 1
            while end < cols and _matches_at(board, base + end, color):
                end += 1
            length = end - col
            if length >= 3:
                runs.append(Run(list(range(base + col, base + end)), color, True))
            col = end

    for col in range(cols):
        row = 0
        while row < rows:
            index = row * cols + col
            value = cells[index]
            if value < 0 or (is_special(value) and color_of(value) == WILDCARD_COLOR):
                row += 1
                continue
            color = color_of(value)
            end = row + 1
            while end < rows and _matches_at(board, end * cols + col, color):
                end += 1
            length = end - row
            if length >= 3:
                runs.append(Run([r * cols + col for r in range(row, end)], color, False))
            row = end

    return runs


def has_match(board: Board) -> bool:
    return bool(find_runs(board))


def swap_candidates(board: Board) -> list[tuple[int, int]]:
    """Échanges adjacents autorisés par les règles du match-3.

    Un échange est légal s'il crée un alignement, ou s'il implique au moins un
    bonus (les bonus se déclenchent toujours par échange).
    """
    candidates: list[tuple[int, int]] = []
    for row in range(board.rows):
        for col in range(board.cols):
            index = board.index(row, col)
            if not board.is_swappable(index):
                continue
            for delta_row, delta_col in ((0, 1), (1, 0)):
                target_row, target_col = row + delta_row, col + delta_col
                if not board.in_bounds(target_row, target_col):
                    continue
                other = board.index(target_row, target_col)
                if not board.is_swappable(other):
                    continue

                first = board.cells[index]
                second = board.cells[other]
                if is_special(first) or is_special(second):
                    candidates.append((index, other))
                    continue

                board.cells[index], board.cells[other] = second, first
                legal = has_match(board)
                board.cells[index], board.cells[other] = first, second
                if legal:
                    candidates.append((index, other))

    return candidates

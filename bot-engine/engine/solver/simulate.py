"""Simulation complète d'un coup : alignements, bonus, gravité, cascades.

Deux modes d'exécution :

* ``rng = None`` — passe déterministe : seul le premier nettoyage est évalué, les
  cases libérées deviennent ``UNKNOWN`` et ne produisent aucun alignement. Sert au
  pré-classement rapide des candidats.
* ``rng`` fourni — tirage Monte-Carlo : les cases sont remplies aléatoirement, les
  cascades se déroulent jusqu'à ``cascade_depth``. Moyenné sur plusieurs tirages,
  c'est ce qui permet de distinguer un coup correct d'un coup réellement fort sur
  les niveaux difficiles.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from ..config import SolverConfig
from ..model import (
    BLOCK,
    CLEARED,
    HOLE,
    KIND_BOMB,
    KIND_DISCO,
    KIND_GENERIC,
    KIND_PLANE,
    KIND_ROCKET_COLUMN,
    KIND_ROCKET_ROW,
    WILDCARD_COLOR,
    color_of,
    encode_special,
    is_special,
    kind_of,
)
from .board import Board, apply_gravity
from .matching import Run, find_runs


@dataclass
class SimulationResult:
    """Bilan chiffré d'une résolution complète."""

    cleared: int = 0
    blocks_destroyed: int = 0
    specials_triggered: int = 0
    cascades: int = 0
    goal_hits: int = 0
    score: float = 0.0
    specials_created: list[int] = field(default_factory=list)
    first_wave: list[int] = field(default_factory=list)

    def merge(self, other: "SimulationResult") -> None:
        self.cleared += other.cleared
        self.blocks_destroyed += other.blocks_destroyed
        self.specials_triggered += other.specials_triggered
        self.goal_hits += other.goal_hits
        self.score += other.score
        self.specials_created.extend(other.specials_created)


def creation_bonus(kind: int, config: SolverConfig) -> float:
    weights = config.weights
    if kind == KIND_DISCO:
        return weights.disco_created
    if kind == KIND_BOMB:
        return weights.bomb_created
    if kind == KIND_PLANE:
        return weights.plane_created
    if kind in (KIND_ROCKET_ROW, KIND_ROCKET_COLUMN):
        return weights.rocket_created
    return weights.rocket_created * 0.8


def area_cells(board: Board, index: int, radius: int) -> set[int]:
    """Cases non statiques dans un carré de rayon donné autour d'une case."""
    row, col = divmod(index, board.cols)
    result: set[int] = set()
    for delta_row in range(-radius, radius + 1):
        target_row = row + delta_row
        if not 0 <= target_row < board.rows:
            continue
        for delta_col in range(-radius, radius + 1):
            target_col = col + delta_col
            if not 0 <= target_col < board.cols:
                continue
            candidate = board.index(target_row, target_col)
            if board.static[candidate]:
                continue
            result.add(candidate)
    return result


def line_cells(board: Board, index: int, horizontal: bool) -> set[int]:
    row, col = divmod(index, board.cols)
    if horizontal:
        indexes = [board.index(row, c) for c in range(board.cols)]
    else:
        indexes = [board.index(r, col) for r in range(board.rows)]
    return {i for i in indexes if not board.static[i]}


def color_cells(board: Board, color: int) -> set[int]:
    return {
        index
        for index, value in enumerate(board.cells)
        if not board.static[index] and value >= 0 and color_of(value) == color
    }


def special_effect(
    board: Board,
    index: int,
    value: int,
    rng: random.Random | None,
    partner_color: int = -1,
) -> set[int]:
    """Cases touchées par le déclenchement d'un bonus."""
    kind = kind_of(value)
    color = color_of(value)

    if kind == KIND_DISCO:
        target_color = partner_color if 0 <= partner_color < board.palette_size else -1
        if target_color < 0:
            target_color = color if 0 <= color < board.palette_size else board.dominant_color()
        affected = color_cells(board, target_color) if target_color >= 0 else set()
        affected.add(index)
        return affected
    if kind == KIND_ROCKET_ROW:
        return line_cells(board, index, horizontal=True)
    if kind == KIND_ROCKET_COLUMN:
        return line_cells(board, index, horizontal=False)
    if kind == KIND_BOMB:
        return area_cells(board, index, radius=2)
    if kind == KIND_PLANE:
        affected = {index}
        pool = [i for i in board.playable_indexes() if i != index]
        if color >= 0:
            same = [i for i in pool if color_of(board.cells[i]) == color]
            pool = same or pool
        if pool:
            picks = rng.sample(pool, min(3, len(pool))) if rng else pool[:3]
            affected.update(picks)
        return affected
    return area_cells(board, index, radius=1)


def plan_creations(runs: list[Run]) -> dict[int, tuple[int, int]]:
    """Détermine les bonus créés par les alignements d'une vague.

    * 5 et plus  -> boule disco ;
    * 4          -> fusée (ligne ou colonne selon l'orientation) ;
    * intersection de deux alignements -> avion.
    """
    creations: dict[int, tuple[int, int]] = {}
    participation: Counter = Counter()

    for run in runs:
        for cell in run.cells:
            participation[cell] += 1

    for run in runs:
        if run.length >= 5:
            creations[run.anchor] = (KIND_DISCO, run.color)
        elif run.length == 4:
            kind = KIND_ROCKET_COLUMN if run.horizontal else KIND_ROCKET_ROW
            creations.setdefault(run.cells[1], (kind, run.color))

    for cell, count in participation.items():
        if count >= 2:
            color = color_of(run_color_for(runs, cell))
            creations[cell] = (KIND_PLANE, color)

    return creations


def run_color_for(runs: list[Run], cell: int) -> int:
    for run in runs:
        if cell in run.cells:
            return run.color
    return -1


def resolve(
    board: Board,
    rng: random.Random | None,
    config: SolverConfig,
    forced: set[int] | None = None,
) -> SimulationResult:
    """Déroule toutes les cascades consécutives à l'état courant du plateau."""
    weights = config.weights
    result = SimulationResult()
    pending: set[int] = set(forced or ())
    depth = 0

    while depth < max(1, config.cascade_depth):
        runs = find_runs(board)
        wave: set[int] = set(pending)
        pending = set()
        for run in runs:
            wave.update(run.cells)
        if not wave:
            break

        creations = plan_creations(runs) if runs else {}

        # Déclenchement en chaîne des bonus pris dans la vague.
        queue = [index for index in wave if is_special(board.cells[index])]
        triggered_here = 0
        while queue:
            index = queue.pop()
            value = board.cells[index]
            if not is_special(value) or board.static[index]:
                continue
            triggered_here += 1
            affected = special_effect(board, index, value, rng)
            for target in affected:
                if target in wave or board.static[target]:
                    continue
                wave.add(target)
                if is_special(board.cells[target]):
                    queue.append(target)

        # Séparation obstacles / cases jouables, puis dégâts de proximité.
        destroyed: set[int] = set()
        playable: set[int] = set()
        for index in wave:
            if board.static[index]:
                continue
            value = board.cells[index]
            if value == BLOCK:
                destroyed.add(index)
            elif value == HOLE or value == CLEARED:
                continue
            else:
                playable.add(index)

        for index in playable | destroyed:
            for neighbor in board.neighbors(index):
                if board.cells[neighbor] == BLOCK and neighbor not in destroyed:
                    destroyed.add(neighbor)

        if not playable and not destroyed:
            break

        # -------------------------------------------------------------- #
        # Notation de la vague
        # -------------------------------------------------------------- #
        step_score = 0.0
        goal_hits = 0
        for index in playable:
            color = color_of(board.cells[index])
            step_score += weights.cell
            if config.goal_color >= 0 and color == config.goal_color:
                goal_hits += 1
                step_score += weights.cell * (weights.goal_color_multiplier - 1.0)

        step_score += len(destroyed) * weights.block
        for kind, _color in creations.values():
            step_score += creation_bonus(kind, config)
        step_score += triggered_here * weights.special_trigger

        result.score += step_score * (1.0 + weights.cascade_step * depth)
        result.cleared += len(playable)
        result.blocks_destroyed += len(destroyed)
        result.specials_triggered += triggered_here
        result.goal_hits += goal_hits
        result.specials_created.extend(kind for kind, _ in creations.values())
        if depth == 0:
            result.first_wave = sorted(playable | destroyed)

        # -------------------------------------------------------------- #
        # Suppression, gravité, pose des bonus créés
        # -------------------------------------------------------------- #
        for index in playable | destroyed:
            board.cells[index] = CLEARED
        apply_gravity(board, rng)

        for index, (kind, color) in creations.items():
            if board.static[index]:
                continue
            if board.cells[index] < 0:
                continue
            # Une boule disco n'a pas de couleur : elle se combine avec n'importe
            # quelle pièce et ne participe jamais à un alignement.
            if kind == KIND_DISCO or color < 0 or color == WILDCARD_COLOR:
                board.cells[index] = encode_special(WILDCARD_COLOR, kind)
            else:
                board.cells[index] = encode_special(color, kind)

        depth += 1
        if rng is None:
            break

    result.cascades = depth
    return result

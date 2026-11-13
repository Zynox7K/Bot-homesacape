"""Recherche du meilleur coup.

Stratégie en deux passes :

1. **Pré-classement déterministe** de tous les échanges légaux (une seule vague de
   nettoyage, aucun tirage) — très rapide, élimine l'essentiel des coups faibles.
2. **Évaluation stochastique** des meilleurs candidats : plusieurs tirages
   Monte-Carlo déroulant les cascades complètes, moyennés, sous contrainte de
   budget temps.

Les échanges de bonus entre eux sont traités analytiquement (effet combiné), car
leur valeur ne dépend pas des tirages de remplissage.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from ..config import SolverConfig
from ..model import KIND_DISCO, KIND_NAMES, color_of, is_special, kind_of
from .board import Board
from .matching import swap_candidates
from .simulate import SimulationResult, resolve, special_effect


@dataclass
class MovePlan:
    """Coup retenu, prêt à être converti en coordonnées puis en geste."""

    from_index: int
    to_index: int
    score: float
    deterministic_score: float
    expected_cleared: float
    expected_blocks: float
    expected_cascades: float
    combo: bool
    reason: str
    cleared_preview: list[int] = field(default_factory=list)
    specials_created: list[int] = field(default_factory=list)


def apply_swap(
    board: Board, a: int, b: int, config: SolverConfig, rng: random.Random | None
) -> tuple[set[int], float, bool, str]:
    """Échange deux cases et calcule l'effet immédiat des bonus impliqués."""
    first = board.cells[a]
    second = board.cells[b]
    board.cells[a], board.cells[b] = second, first

    weights = config.weights
    forced: set[int] = set()
    bonus = 0.0
    combo = False
    label = ""

    first_special = is_special(first)
    second_special = is_special(second)

    if first_special and second_special:
        combo = True
        # Après échange : ``second`` occupe la case ``a``, ``first`` la case ``b``.
        forced |= special_effect(board, a, second, rng)
        forced |= special_effect(board, b, first, rng)
        bonus += 2.0 * weights.special_trigger
        if kind_of(first) == KIND_DISCO and kind_of(second) == KIND_DISCO:
            forced = {
                index for index in range(len(board.cells)) if not board.static[index]
            }
            bonus += board.rows * board.cols * weights.cell
        else:
            bonus += (board.rows + board.cols) * weights.special_combo_cell
        label = "Combo {} + {}".format(
            KIND_NAMES.get(kind_of(first), "bonus"), KIND_NAMES.get(kind_of(second), "bonus")
        )
    elif first_special:
        partner = color_of(second) if second >= 0 else -1
        forced |= special_effect(board, b, first, rng, partner)
        bonus += weights.special_trigger
        label = "Bonus déclenché : {}".format(KIND_NAMES.get(kind_of(first), "bonus"))
    elif second_special:
        partner = color_of(first) if first >= 0 else -1
        forced |= special_effect(board, a, second, rng, partner)
        bonus += weights.special_trigger
        label = "Bonus déclenché : {}".format(KIND_NAMES.get(kind_of(second), "bonus"))

    return forced, bonus, combo, label


# ---------------------------------------------------------------------- #
def rank_moves(
    board: Board, config: SolverConfig
) -> list[tuple[float, int, int, bool, str, SimulationResult]]:
    """Pré-classe tous les échanges légaux par valeur déterministe décroissante."""
    ranked: list[tuple[float, int, int, bool, str, SimulationResult]] = []
    for a, b in swap_candidates(board):
        clone = board.clone()
        forced, bonus, combo, label = apply_swap(clone, a, b, config, None)
        result = resolve(clone, None, config, forced)
        ranked.append((result.score + bonus, a, b, combo, label, result))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def evaluate_playouts(
    board: Board,
    a: int,
    b: int,
    config: SolverConfig,
    master: random.Random,
    deterministic_score: float,
) -> tuple[float, SimulationResult]:
    """Moyenne plusieurs tirages complets pour un échange donné."""
    playouts = max(1, int(config.playouts))
    total = 0.0
    aggregate = SimulationResult()

    for _ in range(playouts):
        rng = random.Random(master.randrange(1 << 31))
        clone = board.clone()
        forced, bonus, _combo, _label = apply_swap(clone, a, b, config, rng)
        result = resolve(clone, rng, config, forced)
        total += result.score + bonus
        result.score = 0.0  # déjà comptabilisé dans ``total``
        aggregate.merge(result)

    average = total / playouts
    scale = 1.0 / playouts
    scaled = SimulationResult(
        cleared=aggregate.cleared * scale,
        blocks_destroyed=aggregate.blocks_destroyed * scale,
        specials_triggered=aggregate.specials_triggered * scale,
        cascades=aggregate.cascades * scale,
        goal_hits=aggregate.goal_hits * scale,
        score=average,
        specials_created=list(aggregate.specials_created),
        first_wave=list(aggregate.first_wave),
    )

    # Un tirage unique correspond exactement à la passe déterministe : on mélange
    # les deux estimations pour réduire le bruit lorsque ``playouts`` est faible.
    noise = 1.0 if playouts <= 1 else 0.75
    blended = noise * average + (1.0 - noise) * deterministic_score
    return blended, scaled


def best_move(board: Board, config: SolverConfig) -> MovePlan | None:
    """Retourne le meilleur coup disponible, ou ``None`` si aucun échange n'est légal.

    ``time_budget_ms`` borne la seconde passe : au-delà, les candidats restants ne
    sont pas tirés et le meilleur coup déjà évalué est conservé. Cette soupape
    garantit la latence en production, mais elle rend le choix dépendant de la
    vitesse de la machine — une valeur nulle ou négative la désactive et donne une
    recherche complète, parfaitement reproductible.
    """
    started = time.perf_counter()
    ranked = rank_moves(board, config)
    if not ranked:
        return None

    top = ranked[: max(1, int(config.candidates))]
    if len(top) == 1 or config.playouts <= 1:
        score, a, b, combo, label, result = top[0]
        return _build_plan(board, config, a, b, score, score, combo, label, result, result)

    deadline = None
    if config.time_budget_ms > 0:
        deadline = started + max(0.01, config.time_budget_ms / 1000.0)
    master = random.Random(config.random_seed)

    best: tuple[float, int, int, bool, str, SimulationResult, SimulationResult] | None = None
    rows_denominator = max(1, board.rows - 1)

    for deterministic_score, a, b, combo, label, result in top:
        if deadline is not None and best is not None and time.perf_counter() > deadline:
            break
        blended, expected = evaluate_playouts(board, a, b, config, master, deterministic_score)
        position = config.position_bias * (
            max(board.row_of(a), board.row_of(b)) / rows_denominator
        )
        final = blended + position
        if best is None or final > best[0]:
            best = (final, a, b, combo, label, result, expected)

    assert best is not None
    score, a, b, combo, label, deterministic_result, expected = best
    deterministic_score = next(
        (item[0] for item in top if item[1] == a and item[2] == b), score
    )
    return _build_plan(
        board,
        config,
        a,
        b,
        score,
        deterministic_score,
        combo,
        label,
        deterministic_result,
        expected,
    )


def _build_plan(
    board: Board,
    config: SolverConfig,
    a: int,
    b: int,
    score: float,
    deterministic_score: float,
    combo: bool,
    label: str,
    deterministic_result: SimulationResult,
    expected: SimulationResult,
) -> MovePlan:
    reason = _describe(
        board,
        combo,
        label,
        deterministic_result,
        expected,
        config,
    )
    return MovePlan(
        from_index=a,
        to_index=b,
        score=float(score),
        deterministic_score=float(deterministic_score),
        expected_cleared=float(expected.cleared),
        expected_blocks=float(expected.blocks_destroyed),
        expected_cascades=float(expected.cascades),
        combo=combo,
        reason=reason,
        cleared_preview=list(deterministic_result.first_wave),
        specials_created=list(deterministic_result.specials_created),
    )


def _describe(
    board: Board,
    combo: bool,
    label: str,
    deterministic: SimulationResult,
    expected: SimulationResult,
    config: SolverConfig,
) -> str:
    """Explication lisible du choix, renvoyée au client pour affichage."""
    parts: list[str] = []
    if combo and label:
        parts.append(label)
    elif label:
        parts.append(label)

    created = {KIND_NAMES.get(kind, "bonus") for kind in deterministic.specials_created}
    if created:
        parts.append("crée " + ", ".join(sorted(created)))

    blocks = expected.blocks_destroyed
    if blocks >= 0.5:
        parts.append(f"{blocks:.0f} obstacle(s)")

    cascades = expected.cascades
    if cascades >= 1.8:
        parts.append(f"cascade x{cascades:.1f}")

    cleared = expected.cleared
    parts.append(f"{cleared:.0f} case(s) libérée(s)")

    if config.goal_color >= 0 and expected.goal_hits >= 0.5:
        parts.append(f"{expected.goal_hits:.0f} pièce(s) prioritaire(s)")

    return " · ".join(parts)

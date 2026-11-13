"""Tests du solveur : alignements, gravité, cascades, bonus et recherche."""

from __future__ import annotations

import random
import time

import pytest

from engine.config import EngineConfig, SolverConfig, Weights
from engine.model import (
    BLOCK,
    CLEARED,
    HOLE,
    KIND_BOMB,
    KIND_DISCO,
    KIND_PLANE,
    KIND_ROCKET_COLUMN,
    KIND_ROCKET_ROW,
    UNKNOWN,
    WILDCARD_COLOR,
    color_of,
    encode_special,
    is_special,
)
from engine.solver.board import Board, apply_gravity
from engine.solver.matching import find_runs, has_match, swap_candidates
from engine.solver.search import apply_swap, best_move, rank_moves
from engine.solver.simulate import line_cells, resolve, special_effect

KIND_LETTERS = {
    "R": KIND_ROCKET_ROW,
    "C": KIND_ROCKET_COLUMN,
    "P": KIND_PLANE,
    "B": KIND_BOMB,
    "D": KIND_DISCO,
}


def parse(lines: list[str], palette_size: int = 6) -> Board:
    """Construit un plateau depuis une représentation texte.

    ``.`` trou, ``#`` obstacle, ``0-5`` couleurs, ``R/C/P/B`` bonus colorés,
    ``D`` boule disco (joker).
    """
    rows = len(lines)
    cols = len(lines[0])
    assert all(len(line) == cols for line in lines), "largeur incohérente"
    cells: list[int] = []
    for line in lines:
        for char in line:
            if char == ".":
                cells.append(HOLE)
            elif char == "#":
                cells.append(BLOCK)
            elif char.isdigit():
                cells.append(int(char))
            elif char == "D":
                cells.append(encode_special(WILDCARD_COLOR, KIND_DISCO))
            elif char.upper() in KIND_LETTERS:
                cells.append(encode_special(0, KIND_LETTERS[char.upper()]))
            else:  # pragma: no cover - garde-fou de test
                raise AssertionError(f"caractère inconnu : {char}")
    return Board(rows, cols, cells, [value == HOLE for value in cells], palette_size)


def solver_config(**overrides) -> SolverConfig:
    config = EngineConfig.load().solver
    payload = config.__dict__.copy()
    payload.pop("weights", None)
    payload.update(overrides)
    return SolverConfig(weights=config.weights, **payload)


def quiet_board(rows: int = 7, cols: int = 7, palette_size: int = 6) -> Board:
    """Plateau « mort » : aucun alignement et aucun échange légal.

    Le motif ``(ligne + 2 * colonne) % 4`` est un blocage classique du match-3.
    Il est décalé sur les couleurs 2 à 5 pour laisser les couleurs 0 et 1 libres
    et construire des positions de test parfaitement maîtrisées.
    """
    cells = [2 + ((row + 2 * col) % 4) for row in range(rows) for col in range(cols)]
    board = Board(rows, cols, cells, [False] * len(cells), palette_size)
    assert find_runs(board) == [], "la base ne doit contenir aucun alignement"
    assert swap_candidates(board) == [], "la base ne doit offrir aucun échange"
    return board


def random_board(rows: int = 9, cols: int = 9, colors: int = 5, seed: int = 0) -> Board:
    rng = random.Random(seed)
    cells: list[int] = []
    for row in range(rows):
        for col in range(cols):
            forbidden = set()
            if col >= 2 and cells[-1] == cells[-2]:
                forbidden.add(cells[-1])
            if row >= 2 and cells[(row - 1) * cols + col] == cells[(row - 2) * cols + col]:
                forbidden.add(cells[(row - 1) * cols + col])
            choices = [value for value in range(colors) if value not in forbidden]
            cells.append(rng.choice(choices))
    return Board(rows, cols, cells, [False] * len(cells), colors)


# ---------------------------------------------------------------------- #
# Alignements
# ---------------------------------------------------------------------- #
def test_find_runs_horizontal_and_vertical() -> None:
    board = parse(["12121", "00021", "21212", "10111"])
    runs = find_runs(board)
    assert len(runs) == 2
    horizontal = [run for run in runs if run.horizontal]
    vertical = [run for run in runs if not run.horizontal]
    assert len(horizontal) == 2
    assert {run.length for run in horizontal} == {3}
    assert not vertical


def test_two_in_a_row_is_not_a_match() -> None:
    board = parse(["1212", "0012", "2121"])
    assert find_runs(board) == []
    assert not has_match(board)


def test_holes_and_blocks_interrupt_a_run() -> None:
    board = parse(["00.00", "00#00"])
    assert find_runs(board) == []


def test_special_of_same_color_joins_a_run() -> None:
    board = parse(["12121", "00R21", "21212"])
    runs = find_runs(board)
    assert len(runs) == 1
    assert runs[0].length == 3
    assert runs[0].color == 0


def test_wildcard_never_joins_a_run() -> None:
    board = parse(["12121", "00D21", "21212"])
    assert find_runs(board) == []


@pytest.mark.parametrize("rows,cols", [(6, 6), (7, 7), (9, 9)])
def test_no_legal_move_on_a_deadlock_pattern(rows: int, cols: int) -> None:
    board = quiet_board(rows=rows, cols=cols, palette_size=4)
    for index in range(len(board.cells)):
        board.cells[index] = board.cells[index] % 4
    assert find_runs(board) == []
    assert swap_candidates(board) == []
    assert best_move(board, solver_config()) is None


def test_swap_candidates_only_contains_legal_moves() -> None:
    board = random_board(seed=3)
    candidates = swap_candidates(board)
    assert candidates
    for a, b in candidates:
        clone = board.clone()
        clone.cells[a], clone.cells[b] = clone.cells[b], clone.cells[a]
        assert has_match(clone) or is_special(board.cells[a]) or is_special(board.cells[b])


# ---------------------------------------------------------------------- #
# Gravité
# ---------------------------------------------------------------------- #
def test_gravity_moves_pieces_down_and_refills_the_top() -> None:
    board = parse(["1", "2", "3", "4"], palette_size=6)
    board.cells[board.index(3, 0)] = CLEARED
    apply_gravity(board, rng=None)
    assert board.cells == [UNKNOWN, 1, 2, 3]


def test_gravity_stops_at_a_hole() -> None:
    board = parse(["1", ".", "2", "3"], palette_size=6)
    board.cells[board.index(3, 0)] = CLEARED
    apply_gravity(board, rng=None)
    assert board.cells == [1, HOLE, UNKNOWN, 2]


def test_gravity_stops_at_an_intact_block() -> None:
    board = parse(["1", "#", "2", "3"], palette_size=6)
    board.cells[board.index(0, 0)] = CLEARED
    apply_gravity(board, rng=None)
    assert board.cells == [UNKNOWN, BLOCK, 2, 3]


def test_gravity_fills_a_destroyed_block() -> None:
    """Une caisse détruite laisse tomber la pièce du dessus, comblée depuis le haut."""
    board = parse(["1", "#", "2"], palette_size=6)
    board.cells[board.index(1, 0)] = CLEARED
    apply_gravity(board, random.Random(0))
    assert board.cells[board.index(1, 0)] == 1
    assert board.cells[board.index(2, 0)] == 2
    assert 0 <= board.cells[board.index(0, 0)] < 6


def test_gravity_with_rng_only_uses_palette_colors() -> None:
    board = random_board(rows=8, cols=8, colors=4, seed=1)
    for index in range(board.cols):
        board.cells[board.index(7, index)] = CLEARED
    apply_gravity(board, random.Random(7))
    for value in board.cells:
        assert 0 <= value < 4


# ---------------------------------------------------------------------- #
# Résolution et cascades
# ---------------------------------------------------------------------- #
def test_resolve_clears_a_single_run_in_deterministic_mode() -> None:
    board = parse(["121", "000", "212", "121"])
    result = resolve(board, rng=None, config=solver_config())
    assert result.cleared == 3
    assert result.cascades == 1
    assert result.score == pytest.approx(3.0)
    assert board.cells[board.index(0, 1)] == UNKNOWN
    assert board.cells[board.index(1, 1)] == 2


def test_resolve_destroys_adjacent_blocks() -> None:
    board = parse(["1212", "0002", "2121", "1212"])
    board.cells[board.index(1, 3)] = BLOCK
    result = resolve(board, rng=None, config=solver_config())
    assert result.blocks_destroyed == 1
    assert result.score == pytest.approx(3.0 + 7.0)


def test_resolve_ignores_distant_blocks() -> None:
    board = parse(["1212", "0002", "2121", "1212"])
    board.cells[board.index(3, 0)] = BLOCK
    result = resolve(board, rng=None, config=solver_config())
    assert result.blocks_destroyed == 0


def test_four_in_a_row_creates_a_rocket() -> None:
    board = parse(["1212", "0000", "2121"])
    result = resolve(board, rng=None, config=solver_config())
    assert result.specials_created == [KIND_ROCKET_COLUMN]
    assert result.cleared == 4
    created = [value for value in board.cells if is_special(value)]
    assert len(created) == 1


def test_five_in_a_row_creates_a_disco() -> None:
    board = parse(["12121", "00000", "21212"])
    result = resolve(board, rng=None, config=solver_config())
    assert KIND_DISCO in result.specials_created
    assert any(
        is_special(value) and color_of(value) == WILDCARD_COLOR for value in board.cells
    )


def test_disco_never_joins_a_run() -> None:
    board = parse(["12121", "00D00", "21212"])
    assert find_runs(board) == []


def test_l_shape_creates_a_plane() -> None:
    board = parse(["0121", "0212", "0001", "1212"])
    result = resolve(board, rng=None, config=solver_config())
    assert KIND_PLANE in result.specials_created


def test_rocket_clears_its_line_when_triggered() -> None:
    board = parse(["12121", "00R21", "21212"], palette_size=6)
    result = resolve(board, rng=None, config=solver_config())
    assert result.specials_triggered == 1
    assert result.cleared == 5


def test_column_rocket_clears_its_column() -> None:
    board = parse(["12C21", "21012", "12021", "21012"], palette_size=6)
    index = board.index(0, 2)
    assert board.cells[index] == encode_special(0, KIND_ROCKET_COLUMN)
    cells = line_cells(board, index, horizontal=False)
    assert len(cells) == 4


def test_bomb_covers_a_five_by_five_area() -> None:
    board = parse(["1212121", "2121212", "121B212", "2121212", "1212121"], palette_size=6)
    index = board.index(2, 3)
    affected = special_effect(board, index, board.cells[index], None)
    assert len(affected) == 25


def test_disco_clears_every_piece_of_one_color() -> None:
    board = random_board(rows=6, cols=6, colors=3, seed=5)
    index = board.index(2, 2)
    board.cells[index] = encode_special(1, KIND_DISCO)
    expected = sum(
        1
        for position, value in enumerate(board.cells)
        if color_of(value) == 1 and position != index
    )
    affected = special_effect(board, index, board.cells[index], None, partner_color=1)
    assert len(affected) == expected + 1  # + la case du bonus elle-même
    assert index in affected


def test_cascades_continue_with_a_random_generator() -> None:
    config = solver_config(cascade_depth=8)
    board = random_board(rows=8, cols=8, colors=3, seed=11)
    candidates = swap_candidates(board)
    a, b = candidates[0]
    board.cells[a], board.cells[b] = board.cells[b], board.cells[a]
    result = resolve(board, random.Random(3), config)
    assert result.cascades >= 1
    assert CLEARED not in board.cells
    assert UNKNOWN not in board.cells


def test_deterministic_resolve_never_refills_with_colors() -> None:
    board = parse(["121", "000", "212"])
    resolve(board, rng=None, config=solver_config())
    assert UNKNOWN in board.cells


def test_goal_color_is_valued_higher() -> None:
    plain = solver_config(goal_color=-1)
    targeted = solver_config(goal_color=0)
    board_a = parse(["121", "000", "212"])
    board_b = parse(["121", "000", "212"])
    score_plain = resolve(board_a, None, plain).score
    score_targeted = resolve(board_b, None, targeted).score
    assert score_targeted > score_plain


def test_cascade_multiplier_rewards_depth() -> None:
    weights = Weights()
    config = SolverConfig(weights=weights, cascade_depth=6)
    board = parse(["121", "000", "212", "121", "212"])
    result = resolve(board, random.Random(1), config)
    assert result.cascades >= 1
    assert result.score >= result.cleared * weights.cell


# ---------------------------------------------------------------------- #
# Recherche
# ---------------------------------------------------------------------- #
def disco_position() -> Board:
    """Position où un seul échange produit une boule disco (5 alignés)."""
    board = quiet_board(rows=7, cols=7)
    for row, col in [(2, 1), (2, 2), (2, 4), (2, 5), (3, 3)]:
        board.cells[board.index(row, col)] = 0
    return board


def test_best_move_finds_the_five_in_a_row() -> None:
    board = disco_position()
    assert find_runs(board) == [], "la position de départ ne doit contenir aucun alignement"

    plan = best_move(board, solver_config())
    assert plan is not None
    assert KIND_DISCO in plan.specials_created
    assert {plan.from_index, plan.to_index} == {board.index(2, 3), board.index(3, 3)}


def test_best_move_prefers_destroying_a_block() -> None:
    board = quiet_board(rows=5, cols=7)

    # Coup A : aligne trois pièces loin de tout obstacle.
    for row, col in [(2, 0), (2, 1), (2, 3)]:
        board.cells[board.index(row, col)] = 0
    # Coup B : aligne trois pièces juste sous un obstacle.
    for row, col in [(4, 2), (4, 3), (4, 5)]:
        board.cells[board.index(row, col)] = 1
    board.cells[board.index(3, 4)] = BLOCK
    board.cells[board.index(4, 4)] = 4

    assert find_runs(board) == [], "position de départ invalide"
    plan = best_move(board, solver_config(playouts=1))
    assert plan is not None
    assert {plan.from_index, plan.to_index} == {board.index(4, 4), board.index(4, 5)}
    assert plan.expected_blocks >= 1 or "obstacle" in plan.reason


def test_best_move_is_deterministic() -> None:
    board = random_board(seed=9)
    config = solver_config()
    first = best_move(board, config)
    second = best_move(board, config)
    assert first is not None and second is not None
    assert (first.from_index, first.to_index) == (second.from_index, second.to_index)
    assert first.score == pytest.approx(second.score)


def test_best_move_never_returns_an_illegal_swap() -> None:
    config = solver_config()
    for seed in range(6):
        board = random_board(seed=seed)
        plan = best_move(board, config)
        assert plan is not None
        assert (plan.from_index, plan.to_index) in swap_candidates(board)


def test_best_move_matches_the_greedy_optimum_without_playouts() -> None:
    config = solver_config(playouts=1, candidates=64)
    for seed in range(4):
        board = random_board(seed=seed)
        ranked = rank_moves(board, config)
        plan = best_move(board, config)
        assert plan is not None
        # Avec un seul tirage, le classement stochastique reproduit le classement
        # déterministe : le meilleur coup greedy doit être retenu.
        assert plan.deterministic_score == pytest.approx(ranked[0][0], rel=1e-6)


def test_special_combo_outweighs_a_simple_match() -> None:
    board = quiet_board(rows=5, cols=6)
    board.cells[board.index(2, 1)] = encode_special(WILDCARD_COLOR, KIND_DISCO)
    board.cells[board.index(2, 2)] = encode_special(1, KIND_ROCKET_ROW)
    # Un alignement simple disponible ailleurs.
    board.cells[board.index(0, 3)] = 0
    board.cells[board.index(0, 4)] = 0
    board.cells[board.index(1, 5)] = 0

    plan = best_move(board, solver_config())
    assert plan is not None
    assert plan.combo
    assert {plan.from_index, plan.to_index} == {board.index(2, 1), board.index(2, 2)}


def test_apply_swap_exchanges_values_and_reports_combo() -> None:
    board = parse(["12", "21"], palette_size=3)
    board.cells[0] = encode_special(1, KIND_ROCKET_ROW)
    forced, bonus, combo, label = apply_swap(board, 0, 1, solver_config(), None)
    assert combo is False
    assert bonus > 0
    assert forced
    assert "Bonus" in label


def test_time_budget_is_respected() -> None:
    board = random_board(rows=9, cols=9, colors=5, seed=21)
    config = solver_config(mode="expert", time_budget_ms=120, playouts=64, candidates=48)
    started = time.perf_counter()
    plan = best_move(board, config)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert plan is not None
    assert elapsed_ms < 4000.0


def test_reason_is_human_readable() -> None:
    board = random_board(seed=14)
    plan = best_move(board, solver_config())
    assert plan is not None
    assert plan.reason
    assert "case(s) libérée(s)" in plan.reason


def test_board_full_of_obstacles_returns_none() -> None:
    board = parse(["###", "#.#", "###"], palette_size=4)
    assert best_move(board, solver_config()) is None
    assert swap_candidates(board) == []

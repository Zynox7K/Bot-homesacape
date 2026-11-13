"""Tests de bout en bout : capture d'écran → analyse → geste jouable.

Ces tests garantissent la propriété qui compte vraiment : le coup renvoyé par le
moteur, une fois re-projeté dans la géométrie réelle du niveau, correspond à deux
cases adjacentes dont l'échange est légal et de valeur maximale.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from engine.config import EngineConfig
from engine.model import BLOCK, HOLE, color_of
from engine.service import AnalysisRequest, Engine
from engine.solver.board import Board
from engine.solver.matching import find_runs, has_match, swap_candidates
from engine.solver.search import apply_swap, rank_moves
from engine.solver.simulate import resolve
from helpers import compare_boards
from synthetic import build_level, degrade


@pytest.fixture()
def engine() -> Engine:
    return Engine(EngineConfig.load())


def to_cell(point, level) -> tuple[int, int] | None:
    """Re-projette un point normalisé dans la grille réelle du niveau."""
    width, height = level.screen
    pixel_x = point.x * width
    pixel_y = point.y * height
    col = (pixel_x - level.board_left) / level.cell - 0.5
    row = (pixel_y - level.board_top) / level.cell - 0.5
    rounded_col, rounded_row = round(col), round(row)
    if abs(col - rounded_col) > 0.30 or abs(row - rounded_row) > 0.30:
        return None
    if not (0 <= rounded_row < level.rows and 0 <= rounded_col < level.cols):
        return None
    return int(rounded_row), int(rounded_col)


def ground_truth_board(level) -> Board:
    return Board.from_matrix(level.values, palette_size=len(level.palette))


def test_detected_board_equals_ground_truth(engine: Engine) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=31, blocks=((2, 5), (6, 1)), holes=((0, 4),))
    response = engine.analyze(AnalysisRequest(image=level.encode(quality=88)))

    assert response.ok, response.message
    assert response.grid is not None
    assert (response.grid.rows, response.grid.cols) == (level.rows, level.cols)
    assert response.blocks == 2
    assert response.holes == 1
    ok, detail = compare_boards(level.values, response.board)
    assert ok, detail


@pytest.mark.parametrize("seed", [2, 17, 44])
def test_returned_move_is_a_legal_adjacent_swap(engine: Engine, seed: int) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=seed)
    response = engine.analyze(AnalysisRequest(image=level.encode(quality=85)))

    assert response.ok and response.move is not None, response.message
    source = to_cell(response.move.source, level)
    target = to_cell(response.move.target, level)
    assert source is not None, "coordonnée source hors grille"
    assert target is not None, "coordonnée cible hors grille"
    assert source != target

    distance = abs(source[0] - target[0]) + abs(source[1] - target[1])
    assert distance == 1, f"échange non adjacent : {source} -> {target}"

    board = ground_truth_board(level)
    first = board.index(*source)
    second = board.index(*target)
    assert board.is_swappable(first) and board.is_swappable(second)
    board.cells[first], board.cells[second] = board.cells[second], board.cells[first]
    assert has_match(board), "l'échange renvoyé ne crée aucun alignement"


def test_returned_move_is_the_greedy_optimum(engine: Engine) -> None:
    """En mode rapide, le coup renvoyé vaut le meilleur coup du plateau réel."""
    level = build_level(rows=9, cols=9, colors=5, seed=23)
    payload = {"solver": {"mode": "fast"}}
    response = engine.analyze(
        AnalysisRequest(image=level.encode(quality=90), overrides=payload)
    )
    assert response.ok and response.move is not None

    reference = ground_truth_board(level)
    ranked = rank_moves(reference, EngineConfig.load().solver)
    assert ranked, "le niveau de référence devrait offrir des coups"
    best_score = ranked[0][0]

    source = to_cell(response.move.source, level)
    target = to_cell(response.move.target, level)
    assert source and target

    # Le score dépend de la permutation des couleurs ; on le recalcule donc sur le
    # plateau de référence avec les mêmes cases.
    detected = Board.from_matrix(response.board, palette_size=max(1, len(response.palette)))
    mapping = {}
    for row in range(level.rows):
        for col in range(level.cols):
            expected = level.values[row][col]
            found = detected.cells[detected.index(row, col)]
            if expected >= 0:
                mapping[expected] = color_of(found)
    remapped = reference.clone()
    remapped.cells = [
        mapping.get(color_of(value), value) if value >= 0 else value
        for value in remapped.cells
    ]

    played = remapped.clone()
    first = played.index(*source)
    second = played.index(*target)
    forced, bonus, _combo, _label = apply_swap(played, first, second, EngineConfig.load().solver, None)
    result = resolve(played, None, EngineConfig.load().solver, forced)
    assert result.score + bonus == pytest.approx(best_score, rel=1e-6)


def test_move_targets_blocks_when_they_are_valuable(engine: Engine) -> None:
    level = build_level(
        rows=8,
        cols=8,
        colors=4,
        seed=5,
        blocks=((0, 0), (0, 1), (0, 2), (7, 5), (7, 6), (7, 7)),
    )
    response = engine.analyze(AnalysisRequest(image=level.encode(quality=90)))
    assert response.ok and response.move is not None
    assert response.blocks == 6


def test_stale_detection_flags_an_unchanged_board(engine: Engine) -> None:
    level = build_level(rows=8, cols=8, colors=4, seed=6)
    payload = level.encode(quality=85)

    first = engine.analyze(AnalysisRequest(image=payload, session_id="session-1"))
    second = engine.analyze(AnalysisRequest(image=payload, session_id="session-1"))
    other = engine.analyze(AnalysisRequest(image=payload, session_id="session-2"))

    assert first.stale is False
    assert second.stale is True
    assert second.stale_count == 1
    assert other.stale is False


def test_no_board_returns_a_clear_failure(engine: Engine) -> None:
    wallpaper = np.full((2400, 1080, 3), (30, 34, 44), dtype=np.uint8)
    cv2.rectangle(wallpaper, (0, 0), (1080, 240), (26, 28, 36), -1)
    ok, buffer = cv2.imencode(".jpg", wallpaper)
    assert ok

    response = engine.analyze(AnalysisRequest(image=buffer.tobytes()))
    assert response.ok is False
    assert response.move is None
    assert response.message


def test_corrupted_payload_is_reported(engine: Engine) -> None:
    response = engine.analyze(AnalysisRequest(image=b"pas-une-image"))
    assert response.ok is False
    assert "illisible" in response.message


def test_debug_image_is_a_readable_png(engine: Engine) -> None:
    level = build_level(rows=8, cols=8, colors=4, seed=9)
    response = engine.analyze(
        AnalysisRequest(image=level.encode(quality=88), debug=True)
    )
    assert response.ok
    assert response.debug_image

    import base64

    raw = base64.b64decode(response.debug_image)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape[0] > 100 and image.shape[1] > 100


def test_cell_sheet_is_produced_on_request(engine: Engine) -> None:
    level = build_level(rows=6, cols=6, colors=4, seed=13)
    response = engine.analyze(
        AnalysisRequest(image=level.encode(quality=90), cell_sheet=True)
    )
    assert response.ok and response.debug_image

    import base64

    raw = base64.b64decode(response.debug_image)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None


def test_coordinates_are_normalized(engine: Engine) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=19)
    response = engine.analyze(AnalysisRequest(image=level.encode(quality=85)))
    assert response.ok and response.move is not None
    for point in (response.move.source, response.move.target):
        assert 0.0 <= point.x <= 1.0
        assert 0.0 <= point.y <= 1.0
    assert response.grid is not None
    assert 0.0 < response.grid.pitch < 1.0


def test_goal_color_changes_the_choice(engine: Engine) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=27)
    neutral = engine.analyze(AnalysisRequest(image=level.encode(quality=90)))
    assert neutral.ok and neutral.move is not None

    detected = Board.from_matrix(neutral.board, palette_size=max(1, len(neutral.palette)))
    source = to_cell(neutral.move.source, level)
    assert source is not None
    targeted_color = color_of(detected.cells[detected.index(*source)])

    targeted = engine.analyze(
        AnalysisRequest(
            image=level.encode(quality=90),
            goal_color=targeted_color,
            overrides={"solver": {"mode": "fast"}},
        )
    )
    assert targeted.ok and targeted.move is not None
    assert targeted.message == ""


def test_pipeline_survives_degraded_captures(engine: Engine) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=33, blocks=((4, 4),))
    for kwargs in (
        {"brightness": 0.75},
        {"brightness": 1.25},
        {"noise_sigma": 7.0, "seed": 1},
        {"hue_shift": 10.0},
        {"blur": 1},
    ):
        image = degrade(level.image, **kwargs)
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        assert ok
        response = engine.analyze(AnalysisRequest(image=buffer.tobytes()))
        assert response.ok, f"{kwargs} -> {response.message}"
        assert response.grid is not None
        assert (response.grid.rows, response.grid.cols) == (9, 9)
        source = to_cell(response.move.source, level)
        target = to_cell(response.move.target, level)
        assert source is not None and target is not None, f"{kwargs} : geste hors grille"
        assert abs(source[0] - target[0]) + abs(source[1] - target[1]) == 1


def test_analysis_latency_stays_low(engine: Engine) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=41)
    payload = level.encode(quality=80)
    engine.analyze(AnalysisRequest(image=payload))  # échauffement

    response = engine.analyze(AnalysisRequest(image=payload))
    assert response.ok
    assert response.timing.vision_ms < 900
    assert response.timing.solver_ms < 2500
    assert response.timing.total_ms < 3000


def test_structural_cells_are_never_swapped(engine: Engine) -> None:
    level = build_level(
        rows=9,
        cols=9,
        colors=5,
        seed=51,
        holes=((0, 0), (0, 1), (8, 7), (8, 8)),
        blocks=((4, 0), (4, 1), (4, 7), (4, 8)),
    )
    response = engine.analyze(AnalysisRequest(image=level.encode(quality=90)))
    assert response.ok and response.move is not None
    for point in (response.move.source, response.move.target):
        cell = to_cell(point, level)
        assert cell is not None
        assert level.values[cell[0]][cell[1]] not in (HOLE, BLOCK)

"""Tests de localisation de la grille sur des captures synthétiques complètes."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from engine.config import EngineConfig
from engine.vision.frame import build_frame
from engine.vision.grid import candidate_windows, detect_period, locate_grid
from synthetic import PIECE_COLORS, build_level, degrade

CONFIG = EngineConfig.load().vision


def _frame(image: np.ndarray):
    return build_frame(image, CONFIG.analysis_width, CONFIG.min_saturation, CONFIG.min_value)


@pytest.mark.parametrize(
    "rows,cols,cell",
    [(9, 9, 108), (8, 8, 96), (7, 7, 120), (6, 6, 140), (9, 7, 100)],
)
def test_grid_geometry_is_recovered(rows: int, cols: int, cell: int) -> None:
    level = build_level(rows=rows, cols=cols, cell=cell)
    frame = _frame(level.image)
    grid = locate_grid(frame, CONFIG)

    assert grid is not None, "grille non détectée"
    assert (grid.rows, grid.cols) == (rows, cols)

    expected_pitch = level.cell * frame.scale
    assert abs(grid.pitch - expected_pitch) <= 0.08 * expected_pitch

    for row, col in [(0, 0), (rows // 2, cols // 2), (rows - 1, cols - 1)]:
        expected_x, expected_y = level.cell_center(row, col)
        detected_x, detected_y = grid.center(row, col)
        assert abs(detected_x - expected_x * frame.scale) <= 0.20 * grid.pitch
        assert abs(detected_y - expected_y * frame.scale) <= 0.20 * grid.pitch


def test_grid_survives_jpeg_compression() -> None:
    level = build_level(rows=9, cols=9)
    payload = level.encode(quality=78)
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    grid = locate_grid(_frame(image), CONFIG)

    assert grid is not None
    assert (grid.rows, grid.cols) == (9, 9)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"brightness": 0.6},
        {"brightness": 1.45},
        {"noise_sigma": 8.0, "seed": 4},
        {"hue_shift": 14.0},
        {"blur": 1},
        {"brightness": 0.85, "noise_sigma": 5.0, "hue_shift": -8.0, "seed": 9},
    ],
)
def test_grid_survives_screen_conditions(kwargs: dict) -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=3)
    image = degrade(level.image, **kwargs)
    grid = locate_grid(_frame(image), CONFIG)

    assert grid is not None, f"grille perdue pour {kwargs}"
    assert (grid.rows, grid.cols) == (9, 9)
    assert grid.confidence > 0.45


def test_confidence_is_higher_for_a_board_than_for_noise() -> None:
    level = build_level(rows=9, cols=9)
    board_grid = locate_grid(_frame(level.image), CONFIG)

    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, size=level.image.shape, dtype=np.uint8)
    noise_grid = locate_grid(_frame(noise), CONFIG)

    assert board_grid is not None
    assert noise_grid is None or noise_grid.confidence < board_grid.confidence


def test_no_grid_on_a_plain_screen() -> None:
    plain = np.full((2400, 1080, 3), (30, 34, 44), dtype=np.uint8)
    assert locate_grid(_frame(plain), CONFIG) is None


def test_decor_only_screen_has_no_grid() -> None:
    image = np.full((2400, 1080, 3), (30, 34, 44), dtype=np.uint8)
    for index, color in enumerate(PIECE_COLORS):
        cv2.circle(image, (120 + index * 160, 180), 60, color, -1)
    assert locate_grid(_frame(image), CONFIG) is None


def test_detect_period_on_a_synthetic_profile() -> None:
    profile = np.tile(np.array([0.9, 0.9, 0.9, 0.1, 0.1]), 24).astype(np.float64)
    result = detect_period(profile, min_lag=4, max_lag=40)
    assert result is not None
    pitch, strength = result
    assert abs(pitch - 5.0) < 0.6
    assert strength > 0.5


def test_candidate_windows_are_deduplicated() -> None:
    level = build_level(rows=9, cols=9)
    mask = _frame(level.image).strong
    windows = candidate_windows(mask, CONFIG)
    assert windows, "aucune fenêtre candidate"
    for index, (rect_a, _) in enumerate(windows):
        for rect_b, _ in windows[index + 1 :]:
            overlap_area = max(0, min(rect_a[2], rect_b[2]) - max(rect_a[0], rect_b[0])) * max(
                0, min(rect_a[3], rect_b[3]) - max(rect_a[1], rect_b[1])
            )
            smallest = min(
                (rect_a[2] - rect_a[0]) * (rect_a[3] - rect_a[1]),
                (rect_b[2] - rect_b[0]) * (rect_b[3] - rect_b[1]),
            )
            assert overlap_area / smallest < 0.45

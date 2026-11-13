"""Tests de classification des cases : palette, obstacles, trous, bonus."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from engine.config import EngineConfig
from engine.model import BLOCK, HOLE, KIND_BOMB, KIND_DISCO, KIND_ROCKET_ROW, color_of, is_special
from engine.vision.classifier import circular_stats, classify, cluster_palette
from engine.vision.frame import build_frame
from engine.vision.grid import locate_grid
from helpers import compare_boards, count_kind
from synthetic import build_level, degrade

CONFIG = EngineConfig.load()


def _scan(level, image=None, config=None):
    config = config or CONFIG
    source = image if image is not None else level.image
    frame = build_frame(
        source,
        config.vision.analysis_width,
        config.vision.min_saturation,
        config.vision.min_value,
    )
    grid = locate_grid(frame, config.vision)
    assert grid is not None, "grille non détectée"
    return classify(frame, grid, config.vision, None)


@pytest.mark.parametrize("colors", [3, 4, 5, 6])
def test_palette_size_is_recovered(colors: int) -> None:
    level = build_level(rows=8, cols=8, colors=colors, seed=5)
    scan = _scan(level)
    assert scan.color_count == colors
    assert len(scan.palette_hex) == colors


@pytest.mark.parametrize("rows,cols", [(9, 9), (7, 8), (6, 6)])
def test_board_matches_ground_truth(rows: int, cols: int) -> None:
    level = build_level(rows=rows, cols=cols, colors=5, seed=12)
    scan = _scan(level)
    ok, detail = compare_boards(level.values, scan.values)
    assert ok, detail


def test_blocks_and_holes_are_structurally_identified() -> None:
    holes = ((0, 0), (4, 4), (8, 8), (2, 6))
    blocks = ((1, 1), (3, 5), (6, 2), (7, 7))
    level = build_level(rows=9, cols=9, colors=5, seed=21, holes=holes, blocks=blocks)
    scan = _scan(level)

    assert scan.block_count == len(blocks)
    assert scan.hole_count == len(holes)
    for row, col in blocks:
        assert scan.values[row][col] == BLOCK
    for row, col in holes:
        assert scan.values[row][col] == HOLE

    ok, detail = compare_boards(level.values, scan.values)
    assert ok, detail


def test_detection_resists_dark_and_bright_screens() -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=11, blocks=((3, 3), (6, 2)), holes=((0, 8),))
    for kwargs in (
        {"brightness": 0.45},
        {"brightness": 0.7, "noise_sigma": 6.0, "seed": 2},
        {"brightness": 1.4},
        {"hue_shift": -12.0},
        {"blur": 1, "brightness": 0.9},
    ):
        image = degrade(level.image, **kwargs)
        scan = _scan(level, image=image)
        ok, detail = compare_boards(level.values, scan.values)
        assert ok, f"{kwargs} -> {detail}"
        assert scan.confidence > 0.45


def test_detection_resists_jpeg_artifacts() -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=8)
    for quality in (95, 80, 65):
        payload = level.encode(quality=quality)
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        scan = _scan(level, image=image)
        ok, detail = compare_boards(level.values, scan.values)
        assert ok, f"qualité {quality} -> {detail}"


def test_specials_are_flagged() -> None:
    level = build_level(
        rows=8,
        cols=8,
        colors=4,
        seed=17,
        specials=((2, 2, KIND_ROCKET_ROW), (5, 4, KIND_BOMB), (1, 6, KIND_DISCO)),
    )
    scan = _scan(level)
    detected = [
        (row, col)
        for row in range(scan.rows)
        for col in range(scan.cols)
        if is_special(scan.values[row][col])
    ]
    assert scan.special_count >= 1, "aucun bonus détecté"
    assert len(detected) <= 6, f"trop de faux bonus : {detected}"
    # Les pièces ordinaires restent majoritaires.
    assert count_kind(scan.values, BLOCK) == 0
    plain = sum(
        1
        for row in scan.values
        for value in row
        if value >= 0 and not is_special(value)
    )
    assert plain >= scan.rows * scan.cols - 12


def test_circular_stats_handles_wraparound() -> None:
    mean, spread = circular_stats(np.array([359.0, 1.0, 0.0, 358.0]))
    assert abs(mean - 359.5) < 2.0 or abs(mean - 0.0) < 2.0
    assert spread < 5.0

    _, wide = circular_stats(np.array([0.0, 90.0, 180.0, 270.0]))
    assert wide > 60.0


def test_cluster_palette_separates_well_spread_hues() -> None:
    rng = np.random.default_rng(0)
    centers = np.array([10.0, 80.0, 150.0, 220.0, 300.0])
    hues = np.concatenate(
        [(center + rng.normal(0.0, 3.0, 20)) % 360.0 for center in centers]
    )
    labels, detected_centers, spread = cluster_palette(hues, CONFIG.vision)
    assert detected_centers.size == 5
    assert spread < 12.0
    for index in range(5):
        members = labels[labels == index]
        assert members.size >= 10


def test_cluster_palette_never_merges_distinct_colors() -> None:
    rng = np.random.default_rng(1)
    centers = np.array([0.0, 55.0, 118.0, 205.0, 287.0])
    hues = np.concatenate(
        [(center + rng.normal(0.0, 4.0, 14)) % 360.0 for center in centers]
    )
    labels, detected_centers, _ = cluster_palette(hues, CONFIG.vision)
    assert detected_centers.size == 5
    # Chaque teinte d'origine doit être isolée dans son propre groupe.
    for index in range(5):
        block = labels[index * 14 : (index + 1) * 14]
        assert len(set(block.tolist())) == 1


def test_empty_board_returns_nothing() -> None:
    plain = np.full((1200, 540, 3), 40, dtype=np.uint8)
    frame = build_frame(plain, CONFIG.vision.analysis_width, 0.30, 0.22)
    assert locate_grid(frame, CONFIG.vision) is None


def test_color_indices_stay_within_palette() -> None:
    level = build_level(rows=9, cols=9, colors=5, seed=4)
    scan = _scan(level)
    for row in scan.values:
        for value in row:
            assert value in (HOLE, BLOCK) or 0 <= color_of(value) < scan.color_count

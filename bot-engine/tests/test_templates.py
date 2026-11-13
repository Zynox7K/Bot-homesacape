"""Tests de la détection de bonus par gabarits (dossier versionné sur GitHub)."""

from __future__ import annotations

import cv2
import numpy as np

from engine.config import EngineConfig
from engine.model import (
    KIND_BOMB,
    KIND_DISCO,
    KIND_GENERIC,
    KIND_PLANE,
    KIND_ROCKET_COLUMN,
    KIND_ROCKET_ROW,
    is_special,
    kind_of,
)
from engine.vision.classifier import cell_crop, classify
from engine.vision.frame import build_frame
from engine.vision.grid import locate_grid
from engine.vision.templates import TemplateLibrary, _kind_from_name
from synthetic import build_level

CONFIG = EngineConfig.load()


def _scan(level, templates=None):
    frame = build_frame(
        level.image,
        CONFIG.vision.analysis_width,
        CONFIG.vision.min_saturation,
        CONFIG.vision.min_value,
    )
    grid = locate_grid(frame, CONFIG.vision)
    assert grid is not None
    return frame, grid, classify(frame, grid, CONFIG.vision, templates)


def test_template_names_map_to_kinds() -> None:
    assert _kind_from_name("rocket_row.png") == KIND_ROCKET_ROW
    assert _kind_from_name("column-2.png") == KIND_ROCKET_COLUMN
    assert _kind_from_name("plane.png") == KIND_PLANE
    assert _kind_from_name("tnt.png") == KIND_BOMB
    assert _kind_from_name("rainbow_ball.png") == KIND_DISCO
    assert _kind_from_name("inconnu.png") == KIND_GENERIC


def test_library_is_empty_without_directory(tmp_path) -> None:
    library = TemplateLibrary(str(tmp_path / "absent"), size=CONFIG.vision.template_size, threshold=CONFIG.vision.template_threshold)
    assert library.available is False
    assert library.match(np.zeros((32, 32, 3), dtype=np.uint8)) is None


def test_library_matches_its_own_template(tmp_path) -> None:
    level = build_level(rows=7, cols=7, colors=4, seed=61, specials=((3, 3, KIND_BOMB),))
    frame, grid, _scan_result = _scan(level)
    crop = cell_crop(frame, grid, 3, 3)
    assert crop is not None
    cv2.imwrite(str(tmp_path / "bomb.png"), crop)

    library = TemplateLibrary(
        str(tmp_path), size=CONFIG.vision.template_size, threshold=CONFIG.vision.template_threshold
    )
    assert library.available
    assert library.names() == ["bomb"]

    match = library.match(crop)
    assert match is not None
    assert match[0] == KIND_BOMB
    assert match[2] > 0.9

    # Une pièce ordinaire ne doit pas être confondue avec le gabarit.
    false_positives = 0
    for row in range(grid.rows):
        for col in range(grid.cols):
            if (row, col) == (3, 3):
                continue
            other = cell_crop(frame, grid, row, col)
            if other is not None and library.match(other) is not None:
                false_positives += 1
    assert false_positives == 0


def test_templates_drive_classification(tmp_path) -> None:
    level = build_level(
        rows=7, cols=7, colors=4, seed=62, specials=((2, 4, KIND_ROCKET_ROW),)
    )
    frame, grid, _scan_result = _scan(level)
    crop = cell_crop(frame, grid, 2, 4)
    assert crop is not None
    cv2.imwrite(str(tmp_path / "rocket_row.png"), crop)

    library = TemplateLibrary(
        str(tmp_path), size=CONFIG.vision.template_size, threshold=CONFIG.vision.template_threshold
    )
    _frame, _grid, scan = _scan(level, templates=library)

    value = scan.values[2][4]
    assert is_special(value)
    assert kind_of(value) == KIND_ROCKET_ROW
    assert scan.special_count >= 1


def test_template_detection_keeps_the_rest_of_the_board_intact(tmp_path) -> None:
    level = build_level(
        rows=8, cols=8, colors=5, seed=63, specials=((5, 2, KIND_PLANE),), blocks=((1, 1),)
    )
    frame, grid, _scan_result = _scan(level)
    crop = cell_crop(frame, grid, 5, 2)
    cv2.imwrite(str(tmp_path / "plane.png"), crop)

    library = TemplateLibrary(
        str(tmp_path), size=CONFIG.vision.template_size, threshold=CONFIG.vision.template_threshold
    )
    _frame, _grid, scan = _scan(level, templates=library)

    assert scan.block_count == 1
    assert scan.color_count == 5
    assert is_special(scan.values[5][2])
    plain = sum(
        1
        for row in scan.values
        for value in row
        if value >= 0 and not is_special(value)
    )
    assert plain == 8 * 8 - 1 - 1

"""Rendus de diagnostic : superposition annotée et planche de cases.

Ces images sont renvoyées en base64 quand la requête porte ``debug=true``. Elles
servent à vérifier la détection depuis le téléphone et à préparer des gabarits de
bonus (planche de cases à recadrer puis déposer dans ``bot-engine/templates/``).
"""

from __future__ import annotations

import base64

import cv2
import numpy as np

from ..model import BLOCK, HOLE, color_of, is_special, kind_of
from ..solver.search import MovePlan
from .classifier import BoardScan
from .frame import Frame

LINE_COLOR = (255, 255, 255)
MOVE_COLOR = (60, 220, 130)
BLOCK_COLOR = (120, 130, 145)
HOLE_COLOR = (40, 44, 52)


def _hex_to_bgr(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) != 6:
        return (128, 128, 128)
    red = int(text[0:2], 16)
    green = int(text[2:4], 16)
    blue = int(text[4:6], 16)
    return (blue, green, red)


def render_overlay(
    frame: Frame, scan: BoardScan, plan: MovePlan | None, text_lines: list[str]
) -> np.ndarray:
    """Annote la capture : grille, classification, coup retenu, légende."""
    canvas = frame.bgr.copy()
    grid = scan.grid

    cv2.rectangle(
        canvas,
        (int(grid.board_left), int(grid.board_top)),
        (int(grid.board_right), int(grid.board_bottom)),
        (200, 200, 200),
        1,
    )

    for row in range(grid.rows):
        for col in range(grid.cols):
            value = scan.values[row][col]
            center_x, center_y = grid.center(row, col)
            center = (int(center_x), int(center_y))
            radius = max(3, int(grid.pitch * 0.36))

            if value == HOLE:
                color = HOLE_COLOR
            elif value == BLOCK:
                color = BLOCK_COLOR
            else:
                index = color_of(value)
                if 0 <= index < len(scan.palette_hex):
                    color = _hex_to_bgr(scan.palette_hex[index])
                else:
                    color = (230, 230, 230)

            cv2.circle(canvas, center, radius, color, -1, lineType=cv2.LINE_AA)
            if is_special(value):
                cv2.circle(canvas, center, radius, (255, 255, 255), 2, lineType=cv2.LINE_AA)
                cv2.putText(
                    canvas,
                    str(kind_of(value)),
                    (center[0] - 4, center[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (20, 20, 20),
                    1,
                    cv2.LINE_AA,
                )

    # Lignes de la grille.
    half = grid.pitch * 0.5
    for col in range(grid.cols + 1):
        x = int(grid.left - half + col * grid.pitch)
        cv2.line(
            canvas,
            (x, int(grid.top - half)),
            (x, int(grid.top - half + grid.rows * grid.pitch)),
            LINE_COLOR,
            1,
        )
    for row in range(grid.rows + 1):
        y = int(grid.top - half + row * grid.pitch)
        cv2.line(
            canvas,
            (int(grid.left - half), y),
            (int(grid.left - half + grid.cols * grid.pitch), y),
            LINE_COLOR,
            1,
        )

    if plan is not None:
        source_row, source_col = divmod(plan.from_index, grid.cols)
        target_row, target_col = divmod(plan.to_index, grid.cols)
        source = tuple(int(v) for v in grid.center(source_row, source_col))
        target = tuple(int(v) for v in grid.center(target_row, target_col))
        cv2.arrowedLine(canvas, source, target, MOVE_COLOR, max(2, int(grid.pitch * 0.10)),
                        line_type=cv2.LINE_AA, tipLength=0.45)
        for point in (source, target):
            cv2.circle(canvas, point, int(grid.pitch * 0.46), MOVE_COLOR, 2, lineType=cv2.LINE_AA)

    _draw_text_block(canvas, text_lines, (12, 26))
    _draw_legend(canvas, scan)
    return canvas


def _draw_text_block(canvas: np.ndarray, lines: list[str], origin: tuple[int, int]) -> None:
    x, y = origin
    for offset, line in enumerate(lines):
        position = (x, y + offset * 22)
        cv2.rectangle(
            canvas,
            (x - 6, position[1] - 16),
            (x + 8 + int(len(line) * 8.6), position[1] + 6),
            (16, 18, 22),
            -1,
        )
        cv2.putText(
            canvas,
            line,
            position,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (240, 244, 248),
            1,
            cv2.LINE_AA,
        )


def _draw_legend(canvas: np.ndarray, scan: BoardScan) -> None:
    if not scan.palette_hex:
        return
    height, width = canvas.shape[:2]
    size = 22
    block_width = size * len(scan.palette_hex) + 16
    origin_x = max(8, width - block_width)
    origin_y = max(8, height - size - 14)
    cv2.rectangle(
        canvas,
        (origin_x - 6, origin_y - 6),
        (origin_x + block_width - 8, origin_y + size + 6),
        (16, 18, 22),
        -1,
    )
    for index, value in enumerate(scan.palette_hex):
        left = origin_x + index * size
        cv2.rectangle(
            canvas,
            (left + 2, origin_y + 2),
            (left + size - 2, origin_y + size - 2),
            _hex_to_bgr(value),
            -1,
        )
        cv2.putText(
            canvas,
            str(index),
            (left + 7, origin_y + 17),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (18, 18, 18),
            1,
            cv2.LINE_AA,
        )


def render_cell_sheet(frame: Frame, scan: BoardScan, columns: int = 9) -> np.ndarray:
    """Planche des cases individuelles, à recadrer pour créer des gabarits."""
    grid = scan.grid
    cell_size = 64
    padding = 4
    total = grid.rows * grid.cols
    sheet_columns = max(1, min(columns, total))
    sheet_rows = (total + sheet_columns - 1) // sheet_columns
    width = sheet_columns * (cell_size + padding) + padding
    height = sheet_rows * (cell_size + padding) + padding
    sheet = np.full((height, width, 3), 24, dtype=np.uint8)

    half = max(4, int(grid.pitch * 0.42))
    for position in range(total):
        row, col = divmod(position, grid.cols)
        center_x, center_y = grid.center(row, col)
        x0 = max(0, int(center_x) - half)
        y0 = max(0, int(center_y) - half)
        x1 = min(frame.width, int(center_x) + half)
        y1 = min(frame.height, int(center_y) + half)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = cv2.resize(frame.bgr[y0:y1, x0:x1], (cell_size, cell_size))
        sheet_row, sheet_col = divmod(position, sheet_columns)
        offset_x = padding + sheet_col * (cell_size + padding)
        offset_y = padding + sheet_row * (cell_size + padding)
        sheet[offset_y : offset_y + cell_size, offset_x : offset_x + cell_size] = crop

    return sheet


def encode_png_base64(image: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        return ""
    return base64.b64encode(buffer.tobytes()).decode("ascii")

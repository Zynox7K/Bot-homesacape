"""Générateur de captures d'écran synthétiques réalistes.

Les tests du moteur s'appuient sur des images complètes (1080×2400) reproduisant
la structure d'un écran de jeu : papier peint sombre, bandeau d'objectif en haut
avec icônes colorées, plateau de jeu sur fond de feutrine, pièces rondes, caisses
carrées, trous de plateau, boutons en bas. Les éléments hors plateau servent de
leurres à la détection de grille.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np

from engine.model import BLOCK, HOLE, encode_special

#: Palette ordonnée pour que tout préfixe reste bien séparé en teinte.
PIECE_COLORS: list[tuple[int, int, int]] = [
    (58, 62, 226),    # rouge     ~1°
    (46, 224, 246),   # jaune     ~53°
    (74, 200, 78),    # vert      ~118°
    (232, 154, 44),   # bleu      ~205°
    (206, 76, 176),   # violet    ~286°
    (30, 130, 245),   # orange    ~28°
]

WALLPAPER = (30, 34, 44)
WALLPAPER_DECOR = (38, 44, 58)
HUD_BAR = (26, 28, 36)
HUD_TEXT = (70, 74, 84)
FELT = (40, 52, 44)
FELT_BORDER = (52, 66, 58)
CRATE = (70, 110, 150)
CRATE_EDGE = (46, 78, 108)
BUTTON_GREEN = (60, 170, 90)
BUTTON_BLUE = (200, 140, 60)


@dataclass
class SyntheticLevel:
    """Niveau synthétique : image, vérité terrain et géométrie exacte."""

    image: np.ndarray
    values: list[list[int]]
    rows: int
    cols: int
    cell: int
    board_left: int
    board_top: int
    screen: tuple[int, int]
    palette: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def pitch(self) -> float:
        return float(self.cell)

    def cell_center(self, row: int, col: int) -> tuple[float, float]:
        return (
            self.board_left + self.cell * (col + 0.5),
            self.board_top + self.cell * (row + 0.5),
        )

    def encode(self, quality: int = 0) -> bytes:
        """Encode l'image comme le ferait le client Android (JPEG ou PNG)."""
        if quality > 0:
            ok, buffer = cv2.imencode(
                ".jpg", self.image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
            )
        else:
            ok, buffer = cv2.imencode(".png", self.image)
        assert ok, "encodage impossible"
        return buffer.tobytes()


def _no_match_matrix(rows: int, cols: int, colors: int, rng: random.Random) -> list[list[int]]:
    """Tirage aléatoire sans aucun alignement préexistant."""
    matrix: list[list[int]] = [[0] * cols for _ in range(rows)]
    for row in range(rows):
        for col in range(cols):
            forbidden: set[int] = set()
            if col >= 2 and matrix[row][col - 1] == matrix[row][col - 2]:
                forbidden.add(matrix[row][col - 1])
            if row >= 2 and matrix[row - 1][col] == matrix[row - 2][col]:
                forbidden.add(matrix[row - 1][col])
            choices = [value for value in range(colors) if value not in forbidden]
            matrix[row][col] = rng.choice(choices)
    return matrix


def _rounded_panel(image: np.ndarray, rect: tuple[int, int, int, int], color, thickness: int = -1) -> None:
    x0, y0, x1, y1 = rect
    radius = max(4, min(x1 - x0, y1 - y0) // 12)
    cv2.rectangle(image, (x0 + radius, y0), (x1 - radius, y1), color, thickness)
    cv2.rectangle(image, (x0, y0 + radius), (x1, y1 - radius), color, thickness)
    for corner_x, corner_y in (
        (x0 + radius, y0 + radius),
        (x1 - radius, y0 + radius),
        (x0 + radius, y1 - radius),
        (x1 - radius, y1 - radius),
    ):
        cv2.circle(image, (corner_x, corner_y), radius, color, thickness)


def _draw_piece(image: np.ndarray, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    blue, green, red = color
    cv2.circle(image, center, radius, color, -1, lineType=cv2.LINE_AA)
    rim = (max(0, blue - 60), max(0, green - 60), max(0, red - 60))
    cv2.circle(image, center, radius, rim, max(2, radius // 12), lineType=cv2.LINE_AA)
    highlight = (
        min(255, blue + 70),
        min(255, green + 70),
        min(255, red + 70),
    )
    offset = max(2, radius // 3)
    cv2.circle(
        image,
        (center[0] - offset, center[1] - offset),
        max(2, radius // 3),
        highlight,
        -1,
        lineType=cv2.LINE_AA,
    )


def _draw_crate(image: np.ndarray, center: tuple[int, int], cell: int) -> None:
    half = int(cell * 0.43)
    x0, y0 = center[0] - half, center[1] - half
    x1, y1 = center[0] + half, center[1] + half
    cv2.rectangle(image, (x0, y0), (x1, y1), CRATE, -1)
    cv2.rectangle(image, (x0, y0), (x1, y1), CRATE_EDGE, 3)
    cv2.line(image, (x0, y0), (x1, y1), CRATE_EDGE, 2)
    cv2.line(image, (x1, y0), (x0, y1), CRATE_EDGE, 2)


def build_level(
    rows: int = 9,
    cols: int = 9,
    cell: int = 108,
    colors: int = 5,
    seed: int = 7,
    holes: tuple[tuple[int, int], ...] = (),
    blocks: tuple[tuple[int, int], ...] = (),
    specials: tuple[tuple[int, int, int], ...] = (),
    screen: tuple[int, int] = (1080, 2400),
    board_top: int | None = None,
) -> SyntheticLevel:
    """Construit un écran de jeu complet et la vérité terrain associée."""
    rng = random.Random(seed)
    width, height = screen
    image = np.full((height, width, 3), WALLPAPER, dtype=np.uint8)

    # Décor du papier peint (faible saturation, jamais « vif »).
    for _ in range(14):
        center = (rng.randrange(width), rng.randrange(height))
        cv2.circle(image, center, rng.randrange(60, 220), WALLPAPER_DECOR, -1, lineType=cv2.LINE_AA)

    # Bandeau d'objectif : icônes colorées qui servent de leurre.
    cv2.rectangle(image, (0, 0), (width, 268), HUD_BAR, -1)
    for index in range(3):
        _draw_piece(image, (110 + index * 132, 134), 44, PIECE_COLORS[index % len(PIECE_COLORS)])
    _rounded_panel(image, (560, 92, 1010, 176), HUD_TEXT)
    _rounded_panel(image, (560, 190, 860, 236), HUD_TEXT)

    # Plateau.
    board_width = cols * cell
    board_height = rows * cell
    board_left = (width - board_width) // 2
    top = board_top if board_top is not None else int(height * 0.34)
    _rounded_panel(
        image,
        (board_left - 18, top - 18, board_left + board_width + 18, top + board_height + 18),
        FELT_BORDER,
    )
    _rounded_panel(
        image,
        (board_left - 8, top - 8, board_left + board_width + 8, top + board_height + 8),
        FELT,
    )

    matrix = _no_match_matrix(rows, cols, colors, rng)
    values: list[list[int]] = [[0] * cols for _ in range(rows)]
    hole_set = set(holes)
    block_set = set(blocks)
    special_map = {(row, col): kind for row, col, kind in specials}

    for row in range(rows):
        for col in range(cols):
            center = tuple(int(v) for v in _center(board_left, top, cell, row, col))
            if (row, col) in hole_set:
                values[row][col] = HOLE
                continue
            if (row, col) in block_set:
                values[row][col] = BLOCK
                _draw_crate(image, center, cell)
                continue
            color_index = matrix[row][col]
            color = PIECE_COLORS[color_index % len(PIECE_COLORS)]
            if (row, col) in special_map:
                kind = special_map[(row, col)]
                values[row][col] = encode_special(color_index, kind)
                _draw_special(image, center, int(cell * 0.42), color, kind)
            else:
                values[row][col] = color_index
                _draw_piece(image, center, int(cell * 0.42), color)

    # Boutons du bas : grands aplats colorés, eux aussi des leurres.
    _rounded_panel(image, (90, height - 268, 500, height - 132), BUTTON_GREEN)
    _rounded_panel(image, (580, height - 268, 990, height - 132), BUTTON_BLUE)

    return SyntheticLevel(
        image=image,
        values=values,
        rows=rows,
        cols=cols,
        cell=cell,
        board_left=board_left,
        board_top=top,
        screen=screen,
        palette=list(PIECE_COLORS[:colors]),
    )


def _center(board_left: int, board_top: int, cell: int, row: int, col: int) -> tuple[float, float]:
    return board_left + cell * (col + 0.5), board_top + cell * (row + 0.5)


def _draw_special(
    image: np.ndarray,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
    kind: int,
) -> None:
    """Bonus : rendu multi-teintes, volontairement différent d'une pièce simple."""
    _draw_piece(image, center, radius, color)
    for angle in range(0, 360, 45):
        radians = np.deg2rad(angle)
        point = (
            int(center[0] + radius * 0.62 * np.cos(radians)),
            int(center[1] + radius * 0.62 * np.sin(radians)),
        )
        tint = PIECE_COLORS[(angle // 45) % len(PIECE_COLORS)]
        cv2.circle(image, point, max(3, radius // 5), tint, -1, lineType=cv2.LINE_AA)
    cv2.circle(image, center, max(3, radius // 4), (250, 250, 250), -1, lineType=cv2.LINE_AA)
    cv2.putText(
        image,
        str(kind),
        (center[0] - radius // 4, center[1] + radius // 3),
        cv2.FONT_HERSHEY_SIMPLEX,
        radius / 60.0,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )


def build_position(
    matrix: list[list[int]],
    rows: int | None = None,
    cols: int | None = None,
    cell: int = 108,
    screen: tuple[int, int] = (1080, 2400),
    board_top: int | None = None,
    holes: tuple[tuple[int, int], ...] = (),
    blocks: tuple[tuple[int, int], ...] = (),
) -> SyntheticLevel:
    """Construit un écran à partir d'une matrice de plateau imposée."""
    rows = rows or len(matrix)
    cols = cols or len(matrix[0])
    width, height = screen
    image = np.full((height, width, 3), WALLPAPER, dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (width, 268), HUD_BAR, -1)
    for index in range(3):
        _draw_piece(image, (110 + index * 132, 134), 44, PIECE_COLORS[index % len(PIECE_COLORS)])

    board_width = cols * cell
    board_height = rows * cell
    board_left = (width - board_width) // 2
    top = board_top if board_top is not None else int(height * 0.34)
    _rounded_panel(
        image,
        (board_left - 18, top - 18, board_left + board_width + 18, top + board_height + 18),
        FELT_BORDER,
    )
    _rounded_panel(
        image,
        (board_left - 8, top - 8, board_left + board_width + 8, top + board_height + 8),
        FELT,
    )

    values: list[list[int]] = [[0] * cols for _ in range(rows)]
    for row in range(rows):
        for col in range(cols):
            center = tuple(int(v) for v in _center(board_left, top, cell, row, col))
            value = matrix[row][col]
            values[row][col] = value
            if value == HOLE:
                continue
            if value == BLOCK:
                _draw_crate(image, center, cell)
                continue
            from engine.model import color_of, is_special, kind_of

            color = PIECE_COLORS[color_of(value) % len(PIECE_COLORS)]
            if is_special(value):
                _draw_special(image, center, int(cell * 0.42), color, kind_of(value))
            else:
                _draw_piece(image, center, int(cell * 0.42), color)

    _rounded_panel(image, (90, height - 268, 500, height - 132), BUTTON_GREEN)
    _rounded_panel(image, (580, height - 268, 990, height - 132), BUTTON_BLUE)

    return SyntheticLevel(
        image=image,
        values=values,
        rows=rows,
        cols=cols,
        cell=cell,
        board_left=board_left,
        board_top=top,
        screen=screen,
        palette=list(PIECE_COLORS),
    )


def encode_array(image: np.ndarray, quality: int = 85) -> bytes:
    """Encode une image modifiée comme le ferait le client Android (JPEG)."""
    ok, buffer = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    assert ok, "encodage impossible"
    return buffer.tobytes()


def degrade(
    image: np.ndarray,
    brightness: float = 1.0,
    noise_sigma: float = 0.0,
    hue_shift: float = 0.0,
    blur: int = 0,
    seed: int = 0,
) -> np.ndarray:
    """Dégrade une capture pour approcher les conditions réelles d'un téléphone.

    ``brightness`` simule un réglage d'écran, ``noise_sigma`` le bruit du capteur
    ou de la compression, ``hue_shift`` un filtre colorimétrique, ``blur`` une mise
    à l'échelle agressive.
    """
    result = image.astype(np.float32)
    if brightness != 1.0:
        result *= float(brightness)
    if noise_sigma > 0:
        rng = np.random.default_rng(seed)
        result = result + rng.normal(0.0, float(noise_sigma), result.shape)
    result = np.clip(result, 0, 255).astype(np.uint8)

    if hue_shift:
        hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV).astype(np.int16)
        hsv[:, :, 0] = (hsv[:, :, 0] + int(round(hue_shift / 2.0))) % 180
        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    if blur > 0:
        size = int(blur) * 2 + 1
        result = cv2.GaussianBlur(result, (size, size), 0)

    return result

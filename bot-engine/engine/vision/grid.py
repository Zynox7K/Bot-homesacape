"""Localisation de la grille de jeu dans une capture d'écran.

La détection ne suppose rien du thème graphique : elle cherche la région où la
densité de pièces est élevée **et** où cette densité suit une périodicité
régulière. C'est la périodicité — signature d'une grille — qui discrimine le
plateau d'un décor coloré.

Pipeline :

1. masques de pixels « pièce » (strict puis souple) ;
2. recherche des meilleures fenêtres carrées par image intégrale vectorisée, avec
   pénalité de l'anneau voisin et priorité douce au centre bas de l'écran ;
3. suppression des non-maxima pour conserver des fenêtres vraiment distinctes ;
4. pour chaque fenêtre : recadrage sur le contenu, détection de la période par
   autocorrélation FFT sur les profils de projection, alignement de phase, calcul
   d'un indice de confiance ;
5. la grille la plus fiable est retenue.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import VisionConfig
from .frame import Frame

MIN_DIMENSION = 4
MAX_DIMENSION = 12
CANDIDATE_WINDOWS = 12
PER_SIDE_CANDIDATES = 3
NMS_OVERLAP = 0.45


@dataclass
class GridLayout:
    """Grille régulière détectée, en pixels de l'image analysée."""

    left: float
    top: float
    pitch: float
    rows: int
    cols: int
    confidence: float
    board_left: float
    board_top: float
    board_right: float
    board_bottom: float
    periodicity: float = 0.0
    contrast: float = 0.0

    def center(self, row: int, col: int) -> tuple[float, float]:
        return self.left + col * self.pitch, self.top + row * self.pitch

    @property
    def width(self) -> float:
        return (self.cols - 1) * self.pitch

    @property
    def height(self) -> float:
        return (self.rows - 1) * self.pitch


# ---------------------------------------------------------------------- #
# Recherche de fenêtres
# ---------------------------------------------------------------------- #
def _integral(mask: np.ndarray) -> np.ndarray:
    """Image intégrale avec une bordure de zéros (indexation exclusive directe)."""
    integral = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    np.cumsum(np.cumsum(mask.astype(np.int64), axis=0), axis=1, out=integral[1:, 1:])
    return integral


def _window_sums(
    integral: np.ndarray, x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray
) -> np.ndarray:
    return integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]


def _overlap(rect_a: np.ndarray, rect_b: np.ndarray) -> float:
    ix0 = max(int(rect_a[0]), int(rect_b[0]))
    iy0 = max(int(rect_a[1]), int(rect_b[1]))
    ix1 = min(int(rect_a[2]), int(rect_b[2]))
    iy1 = min(int(rect_a[3]), int(rect_b[3]))
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = float(ix1 - ix0) * float(iy1 - iy0)
    area_a = float(rect_a[2] - rect_a[0]) * float(rect_a[3] - rect_a[1])
    area_b = float(rect_b[2] - rect_b[0]) * float(rect_b[3] - rect_b[1])
    return intersection / max(1.0, min(area_a, area_b))


def candidate_windows(
    mask: np.ndarray, config: VisionConfig, keep: int = CANDIDATE_WINDOWS
) -> list[tuple[np.ndarray, float]]:
    """Fenêtres carrées candidates, triées par densité et débarrassées des doublons."""
    height, width = mask.shape
    shortest = min(height, width)
    if shortest < 96:
        return []

    integral = _integral(mask)
    side_min = max(64, int(config.board_min_ratio * shortest))
    side_max = int(config.board_max_ratio * shortest)
    if side_max <= side_min:
        side_max = side_min + 1

    step = max(4, shortest // 72)
    side_step = max(6, shortest // 40)

    collected: list[tuple[float, np.ndarray]] = []

    for side in range(side_min, side_max + 1, side_step):
        ring = max(2, int(side * config.ring_ratio))
        xs = np.arange(0, width - side + 1, step, dtype=np.int64)
        ys = np.arange(0, height - side + 1, step, dtype=np.int64)
        if xs.size == 0 or ys.size == 0:
            continue
        grid_x, grid_y = np.meshgrid(xs, ys)

        area = float(side * side)
        inner = _window_sums(integral, grid_x, grid_y, grid_x + side, grid_y + side) / area

        outer_x0 = np.maximum(0, grid_x - ring)
        outer_y0 = np.maximum(0, grid_y - ring)
        outer_x1 = np.minimum(width, grid_x + side + ring)
        outer_y1 = np.minimum(height, grid_y + side + ring)
        outer_total = _window_sums(integral, outer_x0, outer_y0, outer_x1, outer_y1)
        outer_area = np.maximum(
            1, (outer_x1 - outer_x0) * (outer_y1 - outer_y0) - side * side
        )
        outer = (outer_total - inner * area) / outer_area

        center_y = (grid_y + side * 0.5) / float(height)
        inside_prior = (center_y >= config.board_center_low) & (
            center_y <= config.board_center_high
        )
        prior = np.where(inside_prior, config.board_center_prior, 0.0)

        scores = (inner - config.ring_penalty * outer + prior).reshape(-1)
        flat_x = grid_x.reshape(-1)
        flat_y = grid_y.reshape(-1)

        picks = min(PER_SIDE_CANDIDATES, scores.size)
        top_indexes = np.argpartition(scores, -picks)[-picks:]
        for index in top_indexes:
            score = float(scores[index])
            if score <= 0.12:
                continue
            collected.append(
                (
                    score,
                    np.array(
                        [int(flat_x[index]), int(flat_y[index]),
                         int(flat_x[index]) + side, int(flat_y[index]) + side],
                        dtype=np.int64,
                    ),
                )
            )

    collected.sort(key=lambda item: item[0], reverse=True)
    kept: list[tuple[np.ndarray, float]] = []
    for score, rect in collected:
        if any(_overlap(rect, previous) >= NMS_OVERLAP for previous, _ in kept):
            continue
        kept.append((rect, score))
        if len(kept) >= keep:
            break
    return kept


def refine_window(mask: np.ndarray, rect: np.ndarray) -> np.ndarray:
    """Recadre la fenêtre sur le contenu coloré réellement présent."""
    height, width = mask.shape
    x0, y0, x1, y1 = (int(value) for value in rect)
    region = mask[y0:y1, x0:x1]
    if region.size == 0:
        return rect

    column_profile = region.mean(axis=0)
    row_profile = region.mean(axis=1)
    column_threshold = max(0.15, 0.35 * float(column_profile.max()))
    row_threshold = max(0.15, 0.35 * float(row_profile.max()))

    columns = np.flatnonzero(column_profile >= column_threshold)
    rows = np.flatnonzero(row_profile >= row_threshold)
    if columns.size < 8 or rows.size < 8:
        return rect

    new_x0 = x0 + int(columns[0])
    new_x1 = x0 + int(columns[-1]) + 1
    new_y0 = y0 + int(rows[0])
    new_y1 = y0 + int(rows[-1]) + 1

    side = float(min(x1 - x0, y1 - y0))
    if (new_x1 - new_x0) < 0.55 * side or (new_y1 - new_y0) < 0.55 * side:
        return rect
    if (new_x1 - new_x0) < 64 or (new_y1 - new_y0) < 64:
        return rect

    return np.array(
        [new_x0, new_y0, min(width, new_x1), min(height, new_y1)], dtype=np.int64
    )


# ---------------------------------------------------------------------- #
# Périodicité
# ---------------------------------------------------------------------- #
def _autocorrelation(profile: np.ndarray) -> np.ndarray:
    """Autocorrélation normalisée via FFT (signal recentré, padding double)."""
    signal = profile - profile.mean()
    energy = float(np.dot(signal, signal))
    if energy <= 1e-9:
        return np.zeros_like(profile, dtype=np.float64)
    padded = np.fft.rfft(signal, n=2 * signal.size)
    correlation = np.fft.irfft(padded * np.conj(padded), n=2 * signal.size)[: signal.size]
    return correlation / energy


def _parabolic_peak(values: np.ndarray, index: int) -> float:
    """Interpolation parabolique du pic d'autocorrélation (pas sous-pixel)."""
    if index <= 0 or index >= values.size - 1:
        return float(index)
    left = float(values[index - 1])
    center = float(values[index])
    right = float(values[index + 1])
    denominator = left - 2.0 * center + right
    if abs(denominator) < 1e-12:
        return float(index)
    shift = 0.5 * (left - right) / denominator
    return float(index) + float(np.clip(shift, -0.5, 0.5))


def detect_period(profile: np.ndarray, min_lag: int, max_lag: int) -> tuple[float, float] | None:
    """Détecte la période dominante d'un profil de projection.

    Retourne ``(période, force)`` : la force est l'autocorrélation normalisée au
    pic (0 = aucune périodicité, 1 = structure parfaitement régulière).
    """
    if profile.size < 24:
        return None
    min_lag = max(4, int(min_lag))
    max_lag = min(int(max_lag), profile.size // 2)
    if max_lag <= min_lag:
        return None

    correlation = _autocorrelation(profile.astype(np.float64))
    lags = np.arange(min_lag, max_lag + 1)
    segment = correlation[lags]
    peak_index = int(np.argmax(segment))
    strength = float(segment[peak_index])
    pitch = _parabolic_peak(correlation, int(lags[peak_index]))
    if pitch < min_lag * 0.7:
        return None
    return pitch, strength


def align_axis(
    profile: np.ndarray,
    pitch: float,
    origin: float,
    count: int,
    span: float = 0.5,
    step: float = 0.05,
) -> tuple[float, float]:
    """Aligne l'origine d'un axe sur les centres de cases, au vingtième de pas près.

    On balaie les décalages de ``-span`` à ``+span`` fois le pas et l'on retient
    celui qui maximise le contraste entre la densité mesurée aux centres prédits et
    celle mesurée à mi-chemin entre deux centres. Retourne ``(origine, contraste)``.
    """
    size = profile.size
    best_offset = 0.0
    best_contrast = -np.inf
    offsets = np.arange(-span, span + 1e-9, step)
    for factor in offsets:
        offset = float(factor) * pitch
        centers = origin + offset + np.arange(count) * pitch
        gaps = centers + pitch * 0.5
        center_values = _sample_profile(profile, centers, size)
        gap_values = _sample_profile(profile, gaps, size)
        if center_values.size == 0 or gap_values.size == 0:
            continue
        contrast = float(np.mean(center_values) - np.mean(gap_values))
        if contrast > best_contrast:
            best_contrast = contrast
            best_offset = offset
    if not np.isfinite(best_contrast):
        return origin, 0.0
    return origin + best_offset, best_contrast


def _sample_profile(profile: np.ndarray, positions: np.ndarray, size: int) -> np.ndarray:
    positions = np.asarray(positions, dtype=np.float64)
    valid = (positions >= 0) & (positions <= size - 1)
    if not np.any(valid):
        return np.zeros(0)
    return np.interp(positions[valid], np.arange(size), profile)


def grid_from_window(
    mask: np.ndarray, rect: np.ndarray, window_score: float, config: VisionConfig
) -> GridLayout | None:
    """Construit une grille à partir d'une fenêtre candidate, ou ``None``."""
    height, width = mask.shape
    x0, y0, x1, y1 = (int(value) for value in rect)
    region_width = x1 - x0
    region_height = y1 - y0
    if region_width < 64 or region_height < 64:
        return None

    margin_x = int(region_width * 0.12)
    margin_y = int(region_height * 0.12)
    band = mask[y0 + margin_y : y1 - margin_y, x0 + margin_x : x1 - margin_x]
    if band.size == 0 or band.shape[0] < 32 or band.shape[1] < 32:
        return None

    column_profile = band.mean(axis=0).astype(np.float64)
    row_profile = band.mean(axis=1).astype(np.float64)

    min_lag = max(int(min(region_width, region_height) * config.min_pitch_ratio),
                  int(config.min_pitch_px))
    horizontal = detect_period(column_profile, min_lag, int(region_width * config.max_pitch_ratio))
    vertical = detect_period(row_profile, min_lag, int(region_height * config.max_pitch_ratio))
    if horizontal is None or vertical is None:
        return None

    pitch_x, strength_x = horizontal
    pitch_y, strength_y = vertical
    if min(pitch_x, pitch_y) < config.min_pitch_px:
        return None
    if abs(pitch_x - pitch_y) / max(pitch_x, pitch_y) > 0.12:
        pitch = pitch_x if strength_x >= strength_y else pitch_y
    else:
        pitch = 0.5 * (pitch_x + pitch_y)

    cols = int(np.clip(int(round(region_width / pitch)), MIN_DIMENSION, MAX_DIMENSION))
    rows = int(np.clip(int(round(region_height / pitch)), MIN_DIMENSION, MAX_DIMENSION))
    if max(rows, cols) / float(min(rows, cols)) > config.max_aspect_ratio:
        return None

    band_left = x0 + margin_x
    band_top = y0 + margin_y
    origin_x = band_left + (band.shape[1] - (cols - 1) * pitch) * 0.5
    origin_y = band_top + (band.shape[0] - (rows - 1) * pitch) * 0.5
    origin_x, contrast_x = align_axis(column_profile, pitch, origin_x - band_left, cols)
    origin_y, contrast_y = align_axis(row_profile, pitch, origin_y - band_top, rows)
    origin_x += band_left
    origin_y += band_top

    periodicity = 0.5 * (strength_x + strength_y)
    if periodicity < config.min_periodicity:
        return None
    contrast = 0.5 * (contrast_x + contrast_y)
    if contrast <= 0.02:
        return None

    coverage = float(mask[y0:y1, x0:x1].mean())
    squareness = 1.0 - min(1.0, abs(region_width - region_height) / float(max(region_width, region_height)))
    pitch_consistency = 1.0 - min(1.0, abs(pitch_x - pitch_y) / max(pitch_x, pitch_y))

    confidence = float(
        np.clip(
            0.42 * min(1.0, periodicity / 0.55)
            + 0.20 * min(1.0, contrast / 0.30)
            + 0.16 * min(1.0, window_score / 0.55)
            + 0.12 * min(1.0, coverage / 0.55)
            + 0.06 * squareness
            + 0.04 * pitch_consistency,
            0.0,
            1.0,
        )
    )

    if origin_x < 0 or origin_y < 0:
        return None
    if origin_x + (cols - 1) * pitch > width or origin_y + (rows - 1) * pitch > height:
        return None

    return GridLayout(
        left=float(origin_x),
        top=float(origin_y),
        pitch=float(pitch),
        rows=rows,
        cols=cols,
        confidence=confidence,
        board_left=float(x0),
        board_top=float(y0),
        board_right=float(x1),
        board_bottom=float(y1),
        periodicity=float(periodicity),
        contrast=float(contrast),
    )


def _presence(
    mask: np.ndarray, centers: np.ndarray, gaps: np.ndarray, radius: int, gap_radius: int
) -> tuple[float, float]:
    """Densité moyenne du masque aux centres prédits puis à mi-chemin entre eux."""
    height, width = mask.shape

    def density(points: np.ndarray, rad: int) -> float:
        values = []
        for x, y in points:
            x0 = max(0, int(round(x)) - rad)
            y0 = max(0, int(round(y)) - rad)
            x1 = min(width, int(round(x)) + rad + 1)
            y1 = min(height, int(round(y)) + rad + 1)
            if x1 <= x0 or y1 <= y0:
                return -1.0
            values.append(float(mask[y0:y1, x0:x1].mean()))
        if not values:
            return -1.0
        return float(np.mean(values))

    return density(centers, radius), density(gaps, gap_radius)


def extend_grid(mask: np.ndarray, grid: GridLayout, config: VisionConfig) -> GridLayout:
    """Étend la grille tant que la structure périodique se poursuit.

    La recherche de fenêtre ne teste que des carrés : un plateau plus haut que large
    (ou l'inverse) n'est donc couvert que partiellement. Une fois le pas connu, on
    prolonge la grille ligne par ligne et colonne par colonne, en vérifiant à chaque
    ajout que des pièces sont bien présentes aux centres prédits et absentes entre
    eux.
    """
    height, width = mask.shape
    pitch = grid.pitch
    rows, cols = grid.rows, grid.cols
    left, top = grid.left, grid.top
    cell_radius = max(1, int(round(pitch * 0.22)))
    gap_radius = max(1, int(round(pitch * 0.11)))
    density_threshold = 0.32
    contrast_threshold = 0.12

    def row_present(center_y: float, origin_x: float, count: int) -> bool:
        centers = np.array([[origin_x + index * pitch, center_y] for index in range(count)])
        gaps = np.array(
            [[origin_x + (index + 0.5) * pitch, center_y] for index in range(count - 1)]
        )
        if centers[:, 1].min() - cell_radius < 0 or centers[:, 1].max() + cell_radius >= height:
            return False
        center_value, gap_value = _presence(mask, centers, gaps, cell_radius, gap_radius)
        if center_value < 0 or gap_value < 0:
            return False
        return (
            center_value >= density_threshold
            and center_value - gap_value >= contrast_threshold
        )

    def column_present(center_x: float, origin_y: float, count: int) -> bool:
        centers = np.array([[center_x, origin_y + index * pitch] for index in range(count)])
        gaps = np.array(
            [[center_x, origin_y + (index + 0.5) * pitch] for index in range(count - 1)]
        )
        if centers[:, 0].min() - cell_radius < 0 or centers[:, 0].max() + cell_radius >= width:
            return False
        center_value, gap_value = _presence(mask, centers, gaps, cell_radius, gap_radius)
        if center_value < 0 or gap_value < 0:
            return False
        return (
            center_value >= density_threshold
            and center_value - gap_value >= contrast_threshold
        )

    for _ in range(MAX_DIMENSION):
        if rows >= MAX_DIMENSION:
            break
        if not row_present(top + rows * pitch, left, cols):
            break
        rows += 1
    for _ in range(MAX_DIMENSION):
        if rows >= MAX_DIMENSION:
            break
        if not row_present(top - pitch, left, cols):
            break
        top -= pitch
        rows += 1
    for _ in range(MAX_DIMENSION):
        if cols >= MAX_DIMENSION:
            break
        if not column_present(left + cols * pitch, top, rows):
            break
        cols += 1
    for _ in range(MAX_DIMENSION):
        if cols >= MAX_DIMENSION:
            break
        if not column_present(left - pitch, top, rows):
            break
        left -= pitch
        cols += 1
    for _ in range(MAX_DIMENSION):
        if rows >= MAX_DIMENSION:
            break
        if not row_present(top + rows * pitch, left, cols):
            break
        rows += 1

    if max(rows, cols) / float(min(rows, cols)) > config.max_aspect_ratio:
        return grid

    return GridLayout(
        left=float(left),
        top=float(top),
        pitch=float(pitch),
        rows=int(rows),
        cols=int(cols),
        confidence=grid.confidence,
        board_left=min(grid.board_left, left - pitch * 0.5),
        board_top=min(grid.board_top, top - pitch * 0.5),
        board_right=max(grid.board_right, left + (cols - 1) * pitch + pitch * 0.5),
        board_bottom=max(grid.board_bottom, top + (rows - 1) * pitch + pitch * 0.5),
        periodicity=grid.periodicity,
        contrast=grid.contrast,
    )


def locate_with_mask(mask: np.ndarray, config: VisionConfig) -> GridLayout | None:
    """Meilleure grille trouvable avec un masque donné."""
    best: GridLayout | None = None
    for rect, window_score in candidate_windows(mask, config):
        refined = refine_window(mask, rect)
        grid = grid_from_window(mask, refined, window_score, config)
        if grid is None:
            grid = grid_from_window(mask, rect, window_score, config)
        if grid is None:
            continue
        grid = extend_grid(mask, grid, config)
        if best is None or grid.confidence > best.confidence or (
            grid.confidence == best.confidence
            and grid.rows * grid.cols > best.rows * best.cols
        ):
            best = grid
    return best


def locate_grid(frame: Frame, config: VisionConfig) -> GridLayout | None:
    """Localise la grille en essayant le masque strict puis le masque souple."""
    best: GridLayout | None = None
    for mask in (frame.strong, frame.vivid):
        if not mask.any():
            continue
        grid = locate_with_mask(mask, config)
        if grid is not None and (best is None or grid.confidence > best.confidence):
            best = grid
    return best

"""Classification des cases : palette adaptative, obstacles, trous et bonus.

Aucune couleur n'est codée en dur. Les teintes réellement présentes dans la
capture sont regroupées par k-moyennes circulaire, ce qui rend la détection
indépendante du thème graphique, de la luminosité de l'écran et de l'appareil.

Ordre de traitement, du plus fiable au plus subtil :

1. **Structure** — case vide (hors plateau) ou obstacle plein, d'après la
   vivacité du disque central et le remplissage des coins ;
2. **Gabarits** — reconnaissance exacte des bonus si des images de référence sont
   déposées dans ``bot-engine/templates/`` ;
3. **Palette** — regroupement des teintes des pièces ordinaires uniquement (les
   bonus sont multi-colores et fausseraient les centres) ;
4. **Rejets** — teinte hors palette et candidats bonus heuristiques.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import VisionConfig
from ..model import (
    BLOCK,
    HOLE,
    KIND_DISCO,
    KIND_GENERIC,
    WILDCARD_COLOR,
    encode_special,
)
from .frame import Frame
from .grid import GridLayout
from .templates import TemplateLibrary

MAX_CLUSTERS = 12
PLACEHOLDER = -99


@dataclass
class CellReading:
    """Mesures brutes effectuées sur une case."""

    row: int
    col: int
    bgr: tuple[int, int, int]
    hue: float
    saturation: float
    value: float
    hue_spread: float
    vivid_ratio: float
    corner_ratio: float
    edge_density: float
    valid: bool = True


@dataclass
class BoardScan:
    """Résultat complet de l'analyse d'une capture."""

    grid: GridLayout
    values: list[list[int]]
    cell_hex: list[list[str]]
    palette_hex: list[str]
    palette_hues: list[float]
    color_count: int
    piece_ratio: float
    block_count: int
    hole_count: int
    special_count: int
    confidence: float
    background_hex: str
    readings: list[CellReading] = field(default_factory=list, repr=False)

    @property
    def rows(self) -> int:
        return self.grid.rows

    @property
    def cols(self) -> int:
        return self.grid.cols

    def flat_values(self) -> list[int]:
        return [value for row in self.values for value in row]


# ---------------------------------------------------------------------- #
# Statistiques circulaires
# ---------------------------------------------------------------------- #
def circular_stats(hues_deg: np.ndarray) -> tuple[float, float]:
    """Moyenne et écart-type circulaires d'un ensemble de teintes (degrés)."""
    if hues_deg.size == 0:
        return 0.0, 0.0
    radians = np.deg2rad(hues_deg)
    sine = float(np.sin(radians).mean())
    cosine = float(np.cos(radians).mean())
    resultant = math.sqrt(sine * sine + cosine * cosine)
    mean = math.degrees(math.atan2(sine, cosine)) % 360.0
    if resultant <= 1e-6:
        return mean, 180.0
    spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(min(1.0, resultant)))))
    return mean, spread


def circular_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matrice des distances circulaires (degrés) entre deux jeux de teintes."""
    difference = np.abs(a[:, None] - b[None, :])
    return np.minimum(difference, 360.0 - difference)


def circular_mean(hues_deg: np.ndarray) -> float:
    if hues_deg.size == 0:
        return 0.0
    radians = np.deg2rad(hues_deg)
    return math.degrees(math.atan2(float(np.sin(radians).mean()), float(np.cos(radians).mean()))) % 360.0


# ---------------------------------------------------------------------- #
# Échantillonnage des cases
# ---------------------------------------------------------------------- #
def _probe_rect(
    frame: Frame, center_x: float, center_y: float, radius: int
) -> tuple[int, int, int, int] | None:
    x0 = max(0, int(round(center_x)) - radius)
    y0 = max(0, int(round(center_y)) - radius)
    x1 = min(frame.width, int(round(center_x)) + radius + 1)
    y1 = min(frame.height, int(round(center_y)) + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _invalid_reading(row: int, col: int) -> CellReading:
    """Case hors image ou trop petite pour être mesurée."""
    return CellReading(
        row=row,
        col=col,
        bgr=(0, 0, 0),
        hue=0.0,
        saturation=0.0,
        value=0.0,
        hue_spread=0.0,
        vivid_ratio=0.0,
        corner_ratio=0.0,
        edge_density=0.0,
        valid=False,
    )


def sample_cells(frame: Frame, grid: GridLayout, config: VisionConfig) -> list[CellReading]:
    """Mesure couleur, vivacité, remplissage des coins et contours de chaque case."""
    radius = max(2.0, grid.pitch * config.cell_sample_ratio)
    half = int(math.ceil(radius))
    offsets_y, offsets_x = np.mgrid[-half : half + 1, -half : half + 1]
    disc = (offsets_x * offsets_x + offsets_y * offsets_y) <= radius * radius

    height, width = frame.bgr.shape[:2]
    readings: list[CellReading] = []

    for row in range(grid.rows):
        for col in range(grid.cols):
            center_x, center_y = grid.center(row, col)
            x0, y0 = int(round(center_x)) - half, int(round(center_y)) - half
            x1, y1 = x0 + 2 * half + 1, y0 + 2 * half + 1
            cx0, cy0 = max(0, x0), max(0, y0)
            cx1, cy1 = min(width, x1), min(height, y1)

            if cx1 <= cx0 or cy1 <= cy0:
                readings.append(_invalid_reading(row, col))
                continue

            mask = disc[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0]
            window_bgr = frame.bgr[cy0:cy1, cx0:cx1]
            window_hsv = frame.hsv[cy0:cy1, cx0:cx1]
            window_vivid = frame.vivid[cy0:cy1, cx0:cx1]
            window_gray = frame.gray[cy0:cy1, cx0:cx1]

            pixels = window_bgr[mask]
            hsv_pixels = window_hsv[mask]
            vivid_pixels = window_vivid[mask]
            if pixels.shape[0] == 0:
                readings.append(_invalid_reading(row, col))
                continue

            mean_bgr = pixels.mean(axis=0)
            # Médiane plutôt que moyenne : robuste aux reflets, contours et
            # textures qui traversent une pièce ou une caisse.
            cell_lab = np.median(frame.lab[cy0:cy1, cx0:cx1][mask], axis=0)
            mean_saturation = float(hsv_pixels[:, 1].mean()) / 255.0
            mean_value = float(hsv_pixels[:, 2].mean()) / 255.0
            vivid_ratio = float(vivid_pixels.mean())

            # La teinte n'a de sens que sur les pixels colorés de la pièce.
            hues = hsv_pixels[vivid_pixels, 0].astype(np.float64) * 2.0
            if hues.size < max(4, int(0.15 * vivid_pixels.size)):
                hues = hsv_pixels[:, 0].astype(np.float64) * 2.0
            hue, hue_spread = circular_stats(hues)

            edges = cv2.Canny(window_gray, 60, 160)
            edge_density = float((edges[mask] > 0).mean())

            readings.append(
                CellReading(
                    row=row,
                    col=col,
                    bgr=(int(mean_bgr[0]), int(mean_bgr[1]), int(mean_bgr[2])),
                    hue=hue,
                    saturation=mean_saturation,
                    value=mean_value,
                    hue_spread=hue_spread,
                    vivid_ratio=vivid_ratio,
                    corner_ratio=_corner_fill_ratio(frame, grid, row, col, cell_lab, config),
                    edge_density=edge_density,
                )
            )

    return readings


def _corner_fill_ratio(
    frame: Frame,
    grid: GridLayout,
    row: int,
    col: int,
    cell_lab: np.ndarray,
    config: VisionConfig,
) -> float:
    """Contraste relatif des quatre coins d'une case par rapport à son fond local.

    Le fond de référence est mesuré **localement**, dans les espaces entre la case
    et ses quatre voisines ; le contraste des coins est ensuite exprimé **en fraction
    du contraste centre/fond** de la case elle-même. Le critère est donc invariant à
    la luminosité de l'écran, à la compression JPEG et aux dégradés du plateau :

    * pièce ronde ou arrondie : les coins retombent sur le fond → ratio ≈ 0 ;
    * obstacle plein (caisse, carton, meuble) : les coins restent dans la matière de
      l'obstacle → ratio élevé.
    """
    center_x, center_y = grid.center(row, col)
    gap_offset = grid.pitch * 0.5
    gap_radius = max(1, int(round(grid.pitch * config.gap_probe_ratio)))

    gap_pixels: list[np.ndarray] = []
    for delta_x, delta_y in (
        (-gap_offset, 0.0),
        (gap_offset, 0.0),
        (0.0, -gap_offset),
        (0.0, gap_offset),
    ):
        rect = _probe_rect(frame, center_x + delta_x, center_y + delta_y, gap_radius)
        if rect is None:
            continue
        patch_lab = frame.lab[rect[1] : rect[3], rect[0] : rect[2]].reshape(-1, 3)
        patch_vivid = frame.vivid[rect[1] : rect[3], rect[0] : rect[2]].reshape(-1)
        # Une pièce débordant sur l'espace inter-cellules ne doit pas fausser la
        # référence : on ne retient que les pixels non vifs du voisinage.
        background_like = patch_lab[~patch_vivid]
        gap_pixels.append(
            background_like if background_like.shape[0] >= 4 else patch_lab
        )
    if not gap_pixels:
        return 0.0

    local_background = np.median(np.concatenate(gap_pixels, axis=0), axis=0).astype(np.float32)
    reference = float(np.linalg.norm(cell_lab.astype(np.float32) - local_background))
    if reference < config.corner_min_contrast:
        # Case sans contraste avec son fond : trou ou fond uniforme.
        return 0.0

    corner_offset = grid.pitch * config.corner_probe_ratio
    corner_radius = max(1, int(round(grid.pitch * config.corner_probe_size)))
    ratios: list[float] = []
    for sign_x in (-1, 1):
        for sign_y in (-1, 1):
            rect = _probe_rect(
                frame,
                center_x + sign_x * corner_offset,
                center_y + sign_y * corner_offset,
                corner_radius,
            )
            if rect is None:
                continue
            patch = frame.lab[rect[1] : rect[3], rect[0] : rect[2]].reshape(-1, 3)
            if patch.shape[0] == 0:
                continue
            median = np.median(patch, axis=0).astype(np.float32)
            ratios.append(
                min(1.0, float(np.linalg.norm(median - local_background)) / reference)
            )

    if not ratios:
        return 0.0
    return float(np.mean(ratios))


# ---------------------------------------------------------------------- #
# Regroupement de la palette
# ---------------------------------------------------------------------- #
def _init_centers(hues: np.ndarray, k: int, rng: np.random.Generator, spread: bool) -> np.ndarray:
    """Initialisation : répartition uniforme ou k-moyennes++ circulaire."""
    if spread or hues.size < k:
        rotation = float(rng.uniform(0.0, 360.0))
        return (np.linspace(0.0, 360.0, k, endpoint=False) + rotation) % 360.0

    centers = [float(hues[int(rng.integers(hues.size))])]
    for _ in range(k - 1):
        distances = circular_distance(hues, np.asarray(centers)).min(axis=1)
        weights = distances ** 2
        total = float(weights.sum())
        if total <= 1e-9:
            centers.append(float(hues[int(rng.integers(hues.size))]))
            continue
        centers.append(float(hues[int(rng.choice(hues.size, p=weights / total))]))
    return np.asarray(centers, dtype=np.float64)


def _kmeans_circular(
    hues: np.ndarray, k: int, restarts: int, iterations: int, seed: int
) -> tuple[np.ndarray, np.ndarray, float]:
    """K-moyennes sur le cercle des teintes.

    Deux garde-fous contre les optimums locaux : initialisation k-moyennes++
    (centres tirés proportionnellement à leur distance aux centres déjà choisis) et
    réaffectation des groupes vides sur le point le plus mal représenté, ce qui
    casse les centres dupliqués.
    """
    rng = np.random.default_rng(seed)
    best_labels = np.zeros(hues.size, dtype=np.int64)
    best_centers = np.zeros(k)
    best_inertia = math.inf

    for restart in range(max(1, restarts)):
        centers = _init_centers(hues, k, rng, spread=(restart % 2 == 1))
        labels = np.zeros(hues.size, dtype=np.int64)

        for _ in range(iterations):
            distances = circular_distance(hues, centers)
            new_labels = np.argmin(distances, axis=1)
            new_centers = centers.copy()
            for index in range(k):
                members = hues[new_labels == index]
                if members.size:
                    new_centers[index] = circular_mean(members)
                else:
                    assigned = distances[np.arange(hues.size), new_labels]
                    new_centers[index] = float(hues[int(np.argmax(assigned))])
            converged = np.array_equal(new_labels, labels) and np.allclose(
                new_centers, centers, atol=1e-3
            )
            labels, centers = new_labels, new_centers
            if converged:
                break

        inertia = float(circular_distance(hues, centers).min(axis=1).mean())
        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = labels
            best_centers = centers

    return best_labels, best_centers, best_inertia


def cluster_palette(
    hues: np.ndarray, config: VisionConfig, seed: int = 20240117
) -> tuple[np.ndarray, np.ndarray, float]:
    """Détermine le nombre de couleurs puis affecte chaque teinte à un groupe.

    Retourne ``(labels, centres triés par teinte, dispersion moyenne)``.
    """
    if hues.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0.0

    upper = int(min(config.palette_max, MAX_CLUSTERS, max(2, hues.size // 3)))
    lower = int(min(config.palette_min, upper))
    minimum_population = max(1.5, config.palette_min_population * hues.size)

    feasible: tuple[np.ndarray, np.ndarray, float, int] | None = None
    fallback: tuple[np.ndarray, np.ndarray, float, float] | None = None

    for k in range(lower, upper + 1):
        labels, centers, inertia = _kmeans_circular(
            hues, k, restarts=8, iterations=32, seed=seed + k * 977
        )
        sizes = np.bincount(labels, minlength=k)
        gaps = _minimum_gap(centers)
        worst_spread = _worst_spread(hues, labels, centers)
        spread = _mean_spread(hues, labels, centers)

        # Un regroupement trop petit fusionne deux couleurs : l'écart interne du
        # groupe fusionné explose. C'est le critère le plus discriminant, car une
        # fusion fait jouer des coups illégaux alors qu'une scission ne fait que
        # masquer des coups possibles.
        penalty = 0.0
        if sizes.min() < minimum_population:
            penalty += (minimum_population - float(sizes.min())) * 0.5
        if gaps < config.palette_min_gap_deg:
            penalty += (config.palette_min_gap_deg - gaps) * 2.0
        if worst_spread > config.palette_max_spread_deg:
            penalty += (worst_spread - config.palette_max_spread_deg) * 3.0
        # Les groupes doivent être nettement séparés : un écart inter-centres
        # faible devant la dispersion interne signale une couleur scindée en deux.
        if gaps < config.separation_ratio * max(1.0, worst_spread):
            penalty += (config.separation_ratio * max(1.0, worst_spread) - gaps) * 1.5

        cost = worst_spread + inertia * 0.5 + penalty
        order = np.argsort(centers)
        remapped = (_remap(labels, order), centers[order], spread)

        if fallback is None or cost < fallback[3]:
            fallback = (remapped[0], remapped[1], remapped[2], cost)
        # Fusionner deux couleurs est plus grave que d'en scinder une : on retient
        # le plus grand nombre de groupes satisfaisant tous les critères.
        if penalty <= 0.0 and (feasible is None or k > feasible[3]):
            feasible = (remapped[0], remapped[1], remapped[2], k)

    if feasible is None:
        if fallback is None:
            return np.zeros(hues.size, dtype=np.int64), np.zeros(0), 0.0
        return fallback[0], fallback[1], fallback[2]
    return feasible[0], feasible[1], feasible[2]


def _remap(labels: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Réindexe les étiquettes selon l'ordre croissant des teintes centres."""
    mapping = np.zeros(order.size, dtype=np.int64)
    mapping[order] = np.arange(order.size)
    return mapping[labels]


def _minimum_gap(centers: np.ndarray) -> float:
    if centers.size < 2:
        return 360.0
    ordered = np.sort(centers)
    deltas = np.diff(ordered)
    wrap = 360.0 - ordered[-1] + ordered[0]
    return float(min(deltas.min(), wrap))


def _mean_spread(hues: np.ndarray, labels: np.ndarray, centers: np.ndarray) -> float:
    if hues.size == 0 or centers.size == 0:
        return 0.0
    distances = circular_distance(hues, centers)
    return float(distances.min(axis=1).mean())


def _worst_spread(hues: np.ndarray, labels: np.ndarray, centers: np.ndarray) -> float:
    """Dispersion moyenne du groupe le plus large (détecte les fusions abusives)."""
    if hues.size == 0 or centers.size == 0:
        return 0.0
    distances = circular_distance(hues, centers)
    assigned = distances.min(axis=1)
    worst = 0.0
    for index in range(centers.size):
        members = assigned[labels == index]
        if members.size:
            worst = max(worst, float(members.mean()))
    return worst


def hue_outliers(hues: np.ndarray, centers: np.ndarray, config: VisionConfig) -> np.ndarray:
    """Marque les teintes qui n'appartiennent à aucun groupe de la palette."""
    if hues.size == 0 or centers.size == 0:
        return np.zeros(hues.size, dtype=bool)
    distances = circular_distance(hues, centers)
    nearest = distances.min(axis=1)
    labels = np.argmin(distances, axis=1)
    spreads = np.array(
        [
            float(nearest[labels == index].mean()) if np.any(labels == index) else 0.0
            for index in range(centers.size)
        ]
    )
    robust = float(np.percentile(nearest, 90)) if nearest.size else 0.0
    tolerance = outlier_tolerance(robust, float(spreads.max(initial=0.0)), config)
    return nearest > tolerance


def outlier_tolerance(robust_spread: float, worst_spread: float, config: VisionConfig) -> float:
    return max(
        config.palette_outlier_deg,
        config.palette_outlier_ratio * max(robust_spread, worst_spread),
    )


def nearest_center(hue: float, centers: np.ndarray) -> tuple[int, float]:
    if centers.size == 0:
        return -1, 360.0
    distances = circular_distance(np.array([hue]), centers)[0]
    index = int(np.argmin(distances))
    return index, float(distances[index])


# ---------------------------------------------------------------------- #
# Fond de plateau et couleurs
# ---------------------------------------------------------------------- #
def estimate_background(frame: Frame, grid: GridLayout) -> tuple[tuple[int, int, int], np.ndarray]:
    """Couleur dominante du fond de plateau (hors pièces)."""
    x0 = max(0, int(grid.board_left))
    y0 = max(0, int(grid.board_top))
    x1 = min(frame.width, int(grid.board_right))
    y1 = min(frame.height, int(grid.board_bottom))
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0), np.zeros(3)

    region_bgr = frame.bgr[y0:y1, x0:x1]
    region_vivid = frame.vivid[y0:y1, x0:x1]
    background_pixels = region_bgr[~region_vivid]
    if background_pixels.shape[0] < 16:
        background_pixels = region_bgr.reshape(-1, 3)

    median = np.median(background_pixels, axis=0)
    lab = cv2.cvtColor(
        np.array([[median.astype(np.uint8)]], dtype=np.uint8), cv2.COLOR_BGR2LAB
    )[0, 0].astype(np.float32)
    return (int(median[0]), int(median[1]), int(median[2])), lab


def _to_lab(bgr: tuple[int, int, int]) -> np.ndarray:
    pixel = np.array([[[bgr[0], bgr[1], bgr[2]]]], dtype=np.uint8)
    return cv2.cvtColor(pixel, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def to_hex(bgr: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, bgr[2])), max(0, min(255, bgr[1])), max(0, min(255, bgr[0]))
    )


def hsv_to_hex(hue: float, saturation: float, value: float) -> str:
    pixel = np.array(
        [[[int(hue / 2.0) % 180, int(saturation * 255), int(value * 255)]]], dtype=np.uint8
    )
    bgr = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return to_hex((int(bgr[0]), int(bgr[1]), int(bgr[2])))


def is_special_candidate(reading: CellReading, config: VisionConfig) -> bool:
    """Un bonus se reconnaît à sa dispersion de teintes et à sa luminosité."""
    return (
        reading.hue_spread > config.special_hue_spread_deg
        and reading.value > config.special_min_value
        and reading.vivid_ratio > 0.45
    )


def cell_crop(frame: Frame, grid: GridLayout, row: int, col: int) -> np.ndarray | None:
    """Extrait le carré central d'une case (entrée du comparateur de gabarits)."""
    half = max(2, int(grid.pitch * 0.40))
    center_x, center_y = grid.center(row, col)
    x0 = max(0, int(center_x) - half)
    y0 = max(0, int(center_y) - half)
    x1 = min(frame.width, int(center_x) + half)
    y1 = min(frame.height, int(center_y) + half)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return frame.bgr[y0:y1, x0:x1]


# ---------------------------------------------------------------------- #
# Classification finale
# ---------------------------------------------------------------------- #
def classify(
    frame: Frame,
    grid: GridLayout,
    config: VisionConfig,
    templates: TemplateLibrary | None = None,
) -> BoardScan:
    """Transforme une capture et une grille en modèle de plateau jouable."""
    readings = sample_cells(frame, grid, config)
    background_bgr, background_lab = estimate_background(frame, grid)

    # ---------------------------------------------------------- 1. structure
    values: list[int] = []
    piece_positions: list[int] = []
    for index, reading in enumerate(readings):
        if not reading.valid:
            values.append(HOLE)
            continue
        if reading.vivid_ratio < config.block_max_vivid:
            distance = float(np.linalg.norm(_to_lab(reading.bgr) - background_lab))
            obstacle = (
                reading.edge_density > config.block_edge_density
                or distance > config.background_tolerance
            )
            values.append(BLOCK if obstacle else HOLE)
            continue
        if reading.corner_ratio > config.block_corner_ratio:
            values.append(BLOCK)
            continue
        values.append(PLACEHOLDER)
        piece_positions.append(index)

    # ---------------------------------------------------------- 2. gabarits
    matched_positions: set[int] = set()
    matched_kinds: dict[int, int] = {}
    if templates is not None and templates.available:
        for index in piece_positions:
            reading = readings[index]
            crop = cell_crop(frame, grid, reading.row, reading.col)
            if crop is None:
                continue
            match = templates.match(crop)
            if match is not None:
                matched_kinds[index] = match[0]
                matched_positions.add(index)

    # ---------------------------------------------------------- 3. palette
    heuristic_specials = {
        index
        for index in piece_positions
        if index not in matched_positions and is_special_candidate(readings[index], config)
    }
    plain_positions = [
        index for index in piece_positions if index not in matched_positions | heuristic_specials
    ]

    hues = np.asarray([readings[index].hue for index in plain_positions], dtype=np.float64)
    labels, centers, spread = cluster_palette(hues, config)

    # Rejet des teintes hors palette : un objet coloré qui n'appartient à aucun
    # groupe (caisse sombre, décor empiétant sur une case) est un obstacle, pas une
    # pièce. Sans ce garde-fou le solveur jouerait des échanges impossibles.
    if hues.size and centers.size:
        rejected = hue_outliers(hues, centers, config)
        if rejected.any() and int((~rejected).sum()) >= 3 * max(2, centers.size):
            kept = ~rejected
            labels, centers, spread = cluster_palette(hues[kept], config)
            relabeled = np.full(hues.size, -1, dtype=np.int64)
            relabeled[kept] = labels
            labels = relabeled

    color_count = int(centers.size)

    for position, index in enumerate(plain_positions):
        label = int(labels[position]) if color_count else 0
        values[index] = BLOCK if label < 0 else label

    # ---------------------------------------------------------- 4. bonus
    special_count = 0
    robust = _mean_spread(hues, np.asarray(labels), centers) if centers.size else 0.0
    tolerance = outlier_tolerance(robust, robust, config)

    for index, kind in matched_kinds.items():
        color = WILDCARD_COLOR if kind == KIND_DISCO else _color_for(readings[index], centers)
        values[index] = encode_special(color, kind)
        special_count += 1

    for index in heuristic_specials:
        reading = readings[index]
        color, distance = nearest_center(reading.hue, centers)
        if color < 0 or distance > tolerance or reading.hue_spread > 2.0 * config.special_hue_spread_deg:
            values[index] = encode_special(WILDCARD_COLOR, KIND_DISCO)
        else:
            values[index] = encode_special(color, KIND_GENERIC)
        special_count += 1

    # ---------------------------------------------------------- synthèse
    palette_hex = [hsv_to_hex(float(hue), 0.85, 0.95) for hue in centers]
    cell_hex = [
        [to_hex(reading.bgr) for reading in readings[row * grid.cols : (row + 1) * grid.cols]]
        for row in range(grid.rows)
    ]
    matrix = [values[row * grid.cols : (row + 1) * grid.cols] for row in range(grid.rows)]

    total = len(readings)
    pieces = sum(1 for value in values if value >= 0)
    piece_ratio = pieces / total if total else 0.0
    blocks = sum(1 for value in values if value == BLOCK)
    holes = sum(1 for value in values if value == HOLE)

    spread_score = 1.0 - min(1.0, spread / max(1.0, config.palette_max_spread_deg * 1.5))
    confidence = float(
        np.clip(
            0.55 * grid.confidence
            + 0.25 * min(1.0, piece_ratio / 0.75)
            + 0.20 * spread_score,
            0.0,
            1.0,
        )
    )

    return BoardScan(
        grid=grid,
        values=matrix,
        cell_hex=cell_hex,
        palette_hex=palette_hex,
        palette_hues=[float(hue) for hue in centers],
        color_count=color_count,
        piece_ratio=piece_ratio,
        block_count=blocks,
        hole_count=holes,
        special_count=special_count,
        confidence=confidence,
        background_hex=to_hex(background_bgr),
        readings=readings,
    )


def _color_for(reading: CellReading, centers: np.ndarray) -> int:
    color, _distance = nearest_center(reading.hue, centers)
    return WILDCARD_COLOR if color < 0 else color

"""Détection des bonus par gabarits d'image.

Les gabarits sont de simples fichiers PNG déposés dans ``bot-engine/templates/``
et versionnés sur le dépôt GitHub : ajouter un gabarit, pousser, redéployer, et la
détection s'enrichit sans aucune modification de l'application Android.

Le nom du fichier détermine le type de bonus reconnu :

``rocket_row.png`` / ``row.png``      -> fusée nettoyant la ligne
``rocket_column.png`` / ``column.png``-> fusée nettoyant la colonne
``plane.png``                         -> avion
``bomb.png`` / ``tnt.png``            -> bombe
``disco.png`` / ``rainbow.png``       -> boule disco
tout autre nom                        -> bonus générique (effet de zone)
"""

from __future__ import annotations

import os
from typing import Iterable

import cv2
import numpy as np

from ..model import (
    KIND_BOMB,
    KIND_DISCO,
    KIND_GENERIC,
    KIND_PLANE,
    KIND_ROCKET_COLUMN,
    KIND_ROCKET_ROW,
)

SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

NAME_TO_KIND: tuple[tuple[tuple[str, ...], int], ...] = (
    (("rocket_row", "row", "horizontal"), KIND_ROCKET_ROW),
    (("rocket_column", "column", "vertical", "col"), KIND_ROCKET_COLUMN),
    (("plane", "paper", "avion"), KIND_PLANE),
    (("bomb", "tnt", "dynamite"), KIND_BOMB),
    (("disco", "rainbow", "ball", "boule"), KIND_DISCO),
)

SCALES = (0.86, 1.0, 1.16)


def _kind_from_name(name: str) -> int:
    stem = os.path.splitext(os.path.basename(name))[0].lower().replace("-", "_")
    for keywords, kind in NAME_TO_KIND:
        if any(keyword in stem for keyword in keywords):
            return kind
    return KIND_GENERIC


class TemplateLibrary:
    """Bibliothèque de gabarits, chargée une fois au démarrage du serveur."""

    def __init__(self, directory: str, size: int = 48, threshold: float = 0.60) -> None:
        self.directory = directory
        self.size = max(16, int(size))
        self.threshold = float(threshold)
        self.templates: list[tuple[str, int, np.ndarray]] = []
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        self.templates = []
        if not self.directory or not os.path.isdir(self.directory):
            return
        for entry in sorted(os.listdir(self.directory)):
            if not entry.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            path = os.path.join(self.directory, entry)
            image = cv2.imread(path, cv2.IMREAD_COLOR)
            if image is None:
                continue
            resized = cv2.resize(
                image, (self.size, self.size), interpolation=cv2.INTER_AREA
            ).astype(np.float32)
            self.templates.append((os.path.splitext(entry)[0], _kind_from_name(entry), resized))

    @property
    def available(self) -> bool:
        return bool(self.templates)

    def names(self) -> list[str]:
        return [name for name, _, _ in self.templates]

    # ------------------------------------------------------------------ #
    def match(self, cell_bgr: np.ndarray) -> tuple[int, str, float] | None:
        """Retourne ``(type, nom, score)`` du meilleur gabarit, ou ``None``."""
        if not self.templates or cell_bgr.size == 0:
            return None

        best_kind = -1
        best_name = ""
        best_score = -1.0
        for scale in SCALES:
            target = max(8, int(round(self.size * scale)))
            crop = cv2.resize(
                cell_bgr, (target, target), interpolation=cv2.INTER_AREA
            ).astype(np.float32)
            for name, kind, template in self.templates:
                resized = cv2.resize(
                    template, (target, target), interpolation=cv2.INTER_AREA
                )
                score = _normalized_correlation(crop, resized)
                if score > best_score:
                    best_score = score
                    best_kind = kind
                    best_name = name

        if best_score >= self.threshold:
            return best_kind, best_name, float(best_score)
        return None


def _normalized_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Corrélation normalisée (équivalent TM_CCOEFF_NORMED sur une position)."""
    left = a - a.mean()
    right = b - b.mean()
    denominator = float(np.sqrt((left * left).sum() * (right * right).sum()))
    if denominator < 1e-6:
        return 0.0
    return float((left * right).sum() / denominator)


def resolve_template_dir(raw: str) -> str:
    """Résout un chemin de gabarits relatif à la racine du moteur."""
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    engine_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(engine_root, raw)


def build_library(
    directory: str, size: int, threshold: float, extra_paths: Iterable[str] = ()
) -> TemplateLibrary:
    library = TemplateLibrary(resolve_template_dir(directory), size, threshold)
    for path in extra_paths:
        if path and os.path.isdir(path) and not library.available:
            library = TemplateLibrary(path, size, threshold)
    return library

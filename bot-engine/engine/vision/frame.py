"""Prétraitement d'image : décodage, mise à l'échelle, espaces colorimétriques.

Toutes les coordonnées renvoyées par le moteur sont **normalisées** (0 à 1) par
rapport à l'image reçue. Le client Android les re-multiplie par la taille réelle
de son écran : le protocole est donc indépendant de la résolution.

Deux masques binaires sont produits :

* ``vivid``  — seuil souple, sert à la classification des cases (présence d'une
  pièce colorée dans une cellule) ;
* ``strong`` — seuil strict (pièces saturées ET lumineuses), sert à localiser la
  grille : il rejette les décors colorés mais sombres d'un écran de jeu.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

STRICT_SATURATION = 0.45
STRICT_VALUE = 0.40


@dataclass
class Frame:
    """Image analysée et ses dérivés colorimétriques."""

    bgr: np.ndarray
    hsv: np.ndarray
    lab: np.ndarray
    gray: np.ndarray
    vivid: np.ndarray
    strong: np.ndarray
    source_width: int
    source_height: int
    scale: float

    @property
    def width(self) -> int:
        return int(self.bgr.shape[1])

    @property
    def height(self) -> int:
        return int(self.bgr.shape[0])

    def to_normalized(self, x: float, y: float) -> tuple[float, float]:
        """Convertit un point en pixels d'analyse vers des coordonnées 0-1."""
        return x / max(1, self.width), y / max(1, self.height)

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        """Convertit un point en pixels d'analyse vers les pixels d'origine."""
        return x / self.scale, y / self.scale


def decode_image(data: bytes) -> np.ndarray:
    """Décode un tampon JPEG/PNG/WebP en BGR uint8.

    Lève ``ValueError`` si le payload n'est pas un tampon d'octets, s'il est vide
    ou si le format n'est pas reconnu.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("payload attendu en octets (JPEG, PNG ou WebP)")
    if len(data) == 0:
        raise ValueError("image vide")
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("format d'image non reconnu")
    return image


def color_mask(hsv: np.ndarray, min_saturation: float, min_value: float) -> np.ndarray:
    """Pixels suffisamment saturés et lumineux, légèrement régularisés."""
    saturation = hsv[:, :, 1] >= int(round(min_saturation * 255.0))
    value = hsv[:, :, 2] >= int(round(min_value * 255.0))
    mask = (saturation & value).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    # Fermeture : bouche les reflets et dessins internes d'une pièce sans
    # élargir sensiblement sa silhouette.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask > 127


def build_frame(
    image: np.ndarray,
    analysis_width: int,
    min_saturation: float,
    min_value: float,
) -> Frame:
    """Réduit l'image et calcule HSV, niveaux de gris et masques de couleur."""
    source_height, source_width = image.shape[:2]
    if analysis_width > 0 and source_width > analysis_width:
        scale = analysis_width / float(source_width)
        target_height = max(1, int(round(source_height * scale)))
        resized = cv2.resize(
            image, (analysis_width, target_height), interpolation=cv2.INTER_AREA
        )
    else:
        scale = 1.0
        resized = image

    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

    return Frame(
        bgr=resized,
        hsv=hsv,
        lab=lab,
        gray=gray,
        vivid=color_mask(hsv, min_saturation, min_value),
        strong=color_mask(hsv, STRICT_SATURATION, STRICT_VALUE),
        source_width=int(source_width),
        source_height=int(source_height),
        scale=float(scale),
    )

"""Encodage commun des cellules, partagé entre la vision et le solveur.

Une cellule est un entier :

* ``HOLE``    : emplacement hors plateau (statique, jamais rempli, jamais jouable) ;
* ``BLOCK``   : obstacle destructible (caisse, carton) — non échangeable, sensible
                aux alignements adjacents ;
* ``UNKNOWN`` : case à remplir (utilisé pendant les simulations déterministes) ;
* ``CLEARED`` : marqueur transient de suppression avant l'application de la gravité ;
* ``0..n-1``  : couleur de pièce ;
* ``>= SPECIAL_OFFSET`` : bonus, encodé ``SPECIAL_OFFSET + couleur * 8 + type``.
"""

from __future__ import annotations

CLEARED = -4
UNKNOWN = -3
HOLE = -2
BLOCK = -1
SPECIAL_OFFSET = 100
SPECIAL_STRIDE = 8
WILDCARD_COLOR = 63

KIND_ROCKET_ROW = 0
KIND_ROCKET_COLUMN = 1
KIND_PLANE = 2
KIND_BOMB = 3
KIND_DISCO = 4
KIND_GENERIC = 5

KIND_NAMES: dict[int, str] = {
    KIND_ROCKET_ROW: "rocket_row",
    KIND_ROCKET_COLUMN: "rocket_column",
    KIND_PLANE: "plane",
    KIND_BOMB: "bomb",
    KIND_DISCO: "disco",
    KIND_GENERIC: "booster",
}

#: Bonus créés selon la longueur d'un alignement.
CREATION_BY_LENGTH = {
    3: None,
    4: KIND_ROCKET_ROW,
    5: KIND_DISCO,
}


def is_piece(value: int) -> bool:
    """Une valeur jouable : couleur simple ou bonus."""
    return value >= 0


def is_special(value: int) -> bool:
    return value >= SPECIAL_OFFSET


def color_of(value: int) -> int:
    if value >= SPECIAL_OFFSET:
        return (value - SPECIAL_OFFSET) // SPECIAL_STRIDE
    return value


def kind_of(value: int) -> int:
    if value >= SPECIAL_OFFSET:
        return (value - SPECIAL_OFFSET) % SPECIAL_STRIDE
    return -1


def encode_special(color: int, kind: int) -> int:
    return SPECIAL_OFFSET + int(color) * SPECIAL_STRIDE + int(kind)


def wildcard() -> int:
    return encode_special(WILDCARD_COLOR, KIND_DISCO)


def describe(value: int) -> str:
    """Description lisible d'une cellule (journalisation, débogage)."""
    if value == HOLE:
        return "hors plateau"
    if value == BLOCK:
        return "obstacle"
    if value == UNKNOWN:
        return "inconnue"
    if value == CLEARED:
        return "libérée"
    if is_special(value):
        return f"bonus {KIND_NAMES.get(kind_of(value), '?')} couleur {color_of(value)}"
    return f"couleur {value}"

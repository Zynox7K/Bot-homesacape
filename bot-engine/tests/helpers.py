"""Utilitaires de comparaison entre vérité terrain et détection."""

from __future__ import annotations

from engine.model import BLOCK, HOLE, color_of


def compare_boards(
    expected: list[list[int]], detected: list[list[int]]
) -> tuple[bool, str]:
    """Vérifie la structure puis l'équivalence des partitions de couleurs.

    Les indices de couleur produits par le regroupement sont arbitraires : seule
    compte la partition (deux pièces de même couleur doivent recevoir le même
    indice, deux couleurs distinctes des indices distincts).
    """
    if len(expected) != len(detected):
        return False, f"nombre de lignes {len(detected)} au lieu de {len(expected)}"
    mapping: dict[int, int] = {}
    for row_index, (expected_row, detected_row) in enumerate(zip(expected, detected)):
        if len(expected_row) != len(detected_row):
            return False, f"ligne {row_index} : largeur différente"
        for col_index, (expected_value, detected_value) in enumerate(
            zip(expected_row, detected_row)
        ):
            position = f"({row_index},{col_index})"
            expected_structural = expected_value == HOLE or expected_value == BLOCK
            detected_structural = detected_value == HOLE or detected_value == BLOCK
            if expected_structural != detected_structural:
                return False, f"{position} : structure {detected_value} au lieu de {expected_value}"
            if expected_structural:
                if expected_value != detected_value:
                    return False, f"{position} : {detected_value} au lieu de {expected_value}"
                continue
            detected_color = color_of(detected_value)
            if expected_value in mapping:
                if mapping[expected_value] != detected_color:
                    return (
                        False,
                        f"{position} : couleur {expected_value} scindée "
                        f"({mapping[expected_value]} puis {detected_color})",
                    )
            else:
                if detected_color in mapping.values():
                    return (
                        False,
                        f"{position} : couleur {expected_value} fusionnée avec une autre",
                    )
                mapping[expected_value] = detected_color
    return True, "identique"


def count_kind(matrix: list[list[int]], value: int) -> int:
    return sum(1 for row in matrix for cell in row if cell == value)

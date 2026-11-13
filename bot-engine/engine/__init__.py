"""Moteur de reconnaissance et de résolution pour jeux de type match-3.

Le paquet est volontairement découpé en deux sous-systèmes indépendants :

* :mod:`engine.vision`  — extraction de la grille et des tuiles depuis une image ;
* :mod:`engine.solver`  — recherche du meilleur coup sur le modèle extrait.

Aucune dépendance à Android ou au réseau : le moteur est testable hors ligne.
"""

__version__ = "1.0.0"

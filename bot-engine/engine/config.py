"""Configuration du moteur : valeurs par défaut, presets et surcharges par requête.

Toute la configuration vit dans ``bot-engine/config.json`` (versionné sur le dépôt
GitHub). Le serveur la charge au démarrage, l'application Android peut la lire via
``GET /config`` et chaque requête ``POST /analyze`` peut apporter des surcharges
ponctuelles sans redéploiement.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any, get_type_hints

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(ENGINE_ROOT, "config.json")


def _merge(target: Any, source: Any) -> Any:
    """Fusionne récursivement ``source`` dans ``target`` (dictionnaires uniquement)."""
    if not isinstance(source, dict):
        return target
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value
    return target


def _from_dict(cls: type, payload: dict[str, Any] | None):
    """Instancie un dataclass en ignorant les clés inconnues (tolérance réseau).

    Les annotations sont résolues via ``get_type_hints`` : avec
    ``from __future__ import annotations`` elles restent des chaînes, ce qui
    empêcherait de détecter les dataclasses imbriqués.
    """
    payload = payload or {}
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in fields(cls):
        if field.name not in payload:
            continue
        value = payload[field.name]
        field_type = hints.get(field.name, field.type)
        if is_dataclass(field_type) and isinstance(value, dict):
            value = _from_dict(field_type, value)
        kwargs[field.name] = value
    return cls(**kwargs)


@dataclass
class VisionConfig:
    """Réglages de la vision par ordinateur (exécutée côté serveur)."""

    analysis_width: int = 540
    min_saturation: float = 0.30
    min_value: float = 0.22
    board_min_ratio: float = 0.30
    board_max_ratio: float = 0.96
    ring_ratio: float = 0.14
    ring_penalty: float = 0.55
    board_center_low: float = 0.20
    board_center_high: float = 0.82
    board_center_prior: float = 0.06
    min_periodicity: float = 0.20
    max_aspect_ratio: float = 1.60
    max_pitch_ratio: float = 0.25
    min_pitch_ratio: float = 0.075
    min_pitch_px: float = 18.0
    cell_sample_ratio: float = 0.30
    corner_probe_ratio: float = 0.38
    corner_probe_size: float = 0.075
    corner_min_contrast: float = 6.0
    gap_probe_ratio: float = 0.06
    block_max_vivid: float = 0.34
    block_corner_ratio: float = 0.30
    block_edge_density: float = 0.10
    background_tolerance: float = 26.0
    palette_min: int = 3
    palette_max: int = 8
    palette_min_gap_deg: float = 16.0
    palette_max_spread_deg: float = 16.0
    palette_min_population: float = 0.02
    separation_ratio: float = 1.8
    palette_outlier_deg: float = 12.0
    palette_outlier_ratio: float = 3.0
    detect_specials: bool = True
    special_hue_spread_deg: float = 40.0
    special_min_value: float = 0.58
    template_dir: str = "templates"
    template_threshold: float = 0.75
    template_size: int = 48


@dataclass
class Weights:
    """Pondérations du solveur. À ajuster selon le type de niveau."""

    cell: float = 1.0
    block: float = 7.0
    goal_color_multiplier: float = 2.2
    cascade_step: float = 0.22
    special_trigger: float = 6.0
    rocket_created: float = 12.0
    plane_created: float = 18.0
    bomb_created: float = 26.0
    disco_created: float = 34.0
    special_combo_cell: float = 1.35
    depth_bonus: float = 0.05


@dataclass
class SolverConfig:
    """Réglages de recherche du meilleur coup."""

    mode: str = "expert"
    candidates: int = 20
    playouts: int = 12
    cascade_depth: int = 7
    time_budget_ms: int = 300
    goal_color: int = -1
    position_bias: float = 0.04
    random_seed: int = 20240117
    weights: Weights = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = Weights()


#: Presets de difficulté. ``mode`` sélectionne un preset, les champs explicites
#: de la configuration restent prioritaires.
MODE_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {"candidates": 8, "playouts": 1, "cascade_depth": 3, "time_budget_ms": 60},
    "balanced": {"candidates": 14, "playouts": 6, "cascade_depth": 5, "time_budget_ms": 150},
    "expert": {"candidates": 24, "playouts": 16, "cascade_depth": 8, "time_budget_ms": 400},
}


@dataclass
class EngineConfig:
    """Configuration complète du moteur."""

    vision: VisionConfig = None  # type: ignore[assignment]
    solver: SolverConfig = None  # type: ignore[assignment]
    default_move_duration_ms: int = 180
    default_settle_ms: int = 1250
    min_capture_interval_ms: int = 1000

    def __post_init__(self) -> None:
        if self.vision is None:
            self.vision = VisionConfig()
        if self.solver is None:
            self.solver = SolverConfig()
        #: Champs du solveur fournis explicitement (non dérivés d'un preset).
        self.solver_explicit_fields: set[str] = set(getattr(self, "solver_explicit_fields", set()))

    # ------------------------------------------------------------------ #
    # Sérialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EngineConfig":
        payload = dict(payload or {})
        vision = _from_dict(VisionConfig, payload.get("vision"))
        solver = _from_dict(SolverConfig, payload.get("solver"))
        config = cls(
            vision=vision,
            solver=solver,
            default_move_duration_ms=int(
                payload.get("default_move_duration_ms", cls.default_move_duration_ms)
            ),
            default_settle_ms=int(payload.get("default_settle_ms", cls.default_settle_ms)),
            min_capture_interval_ms=int(
                payload.get("min_capture_interval_ms", cls.min_capture_interval_ms)
            ),
        )
        # Les champs réellement présents dans la source sont « explicites » : ils
        # ne seront pas écrasés par un preset de difficulté.
        config.solver_explicit_fields = set((payload.get("solver") or {}).keys())
        return config

    @classmethod
    def load(cls, path: str | None = None) -> "EngineConfig":
        """Charge la configuration depuis ``config.json`` (chemin surchargeable)."""
        path = path or os.environ.get("AUTOPLAY_CONFIG", DEFAULT_CONFIG_PATH)
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    # ------------------------------------------------------------------ #
    # Dérivation
    # ------------------------------------------------------------------ #
    def with_overrides(self, overrides: dict[str, Any] | None) -> "EngineConfig":
        """Retourne une copie de la configuration avec ``overrides`` appliqués.

        Une valeur fournie par le client devient explicite : elle survit au choix
        du mode de calcul, alors que les réglages dérivés d'un preset sont
        recalculés si le mode change.
        """
        derived = copy.deepcopy(self)
        derived.solver_explicit_fields = set(self.solver_explicit_fields)
        for section, values in (overrides or {}).items():
            target = getattr(derived, section, None)
            if target is None or not isinstance(values, dict):
                continue
            for key, value in values.items():
                if not hasattr(target, key):
                    continue
                current = getattr(target, key)
                if isinstance(value, dict) and is_dataclass(current):
                    for nested_key, nested_value in value.items():
                        if hasattr(current, nested_key):
                            setattr(current, nested_key, nested_value)
                else:
                    setattr(target, key, value)
                if section == "solver":
                    derived.solver_explicit_fields.add(key)
        return derived.apply_mode()

    def apply_mode(self) -> "EngineConfig":
        """Applique le preset correspondant à ``solver.mode``.

        Seuls les champs absents de la source (fichier ou requête) sont dérivés du
        preset : un réglage explicite prime toujours.
        """
        preset = MODE_PRESETS.get(self.solver.mode)
        if not preset:
            return self
        explicit = getattr(self, "solver_explicit_fields", set())
        for key, value in preset.items():
            if key in explicit:
                continue
            setattr(self.solver, key, value)
        return self

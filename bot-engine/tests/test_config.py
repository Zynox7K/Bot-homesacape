"""Tests de chargement, fusion et dérive de la configuration."""

from __future__ import annotations

import json

from engine.config import MODE_PRESETS, EngineConfig, SolverConfig, VisionConfig


def test_defaults_are_coherent() -> None:
    config = EngineConfig()
    assert config.vision.palette_min <= config.vision.palette_max
    assert config.vision.min_pitch_ratio < config.vision.max_pitch_ratio
    assert config.solver.cascade_depth >= 1
    assert config.solver.weights.cell > 0
    assert config.solver.weights.block > config.solver.weights.cell


def test_load_repository_config() -> None:
    config = EngineConfig.load()
    assert config.solver.mode in MODE_PRESETS
    assert config.vision.analysis_width > 0
    assert isinstance(config.solver.weights.block, float)


def test_nested_weights_are_dataclass_not_dict() -> None:
    payload = {"solver": {"weights": {"block": 12.5}}}
    config = EngineConfig.from_dict(payload)
    assert config.solver.weights.block == 12.5
    # Les valeurs non fournies conservent leur défaut.
    assert config.solver.weights.cell == SolverConfig().weights.cell


def test_unknown_keys_are_ignored() -> None:
    payload = {"vision": {"champ_inconnu": 1}, "autre": 2}
    config = EngineConfig.from_dict(payload)
    assert isinstance(config.vision, VisionConfig)


def test_mode_preset_applies_without_overriding_explicit_values() -> None:
    config = EngineConfig.from_dict({"solver": {"mode": "fast"}})
    config.apply_mode()
    assert config.solver.candidates == MODE_PRESETS["fast"]["candidates"]
    assert config.solver.playouts == MODE_PRESETS["fast"]["playouts"]

    config = EngineConfig.from_dict({"solver": {"mode": "fast", "playouts": 9}})
    config.apply_mode()
    assert config.solver.playouts == 9
    assert config.solver.candidates == MODE_PRESETS["fast"]["candidates"]


def test_overrides_produce_a_new_instance() -> None:
    base = EngineConfig.load()
    derived = base.with_overrides({"solver": {"goal_color": 3}, "vision": {"analysis_width": 320}})
    assert derived.solver.goal_color == 3
    assert derived.vision.analysis_width == 320
    assert base.solver.goal_color == -1
    assert base.vision.analysis_width != 320


def test_round_trip_serialisation() -> None:
    config = EngineConfig.load()
    restored = EngineConfig.from_dict(json.loads(json.dumps(config.to_dict())))
    assert restored.to_dict() == config.to_dict()


def test_save_then_load(tmp_path) -> None:
    config = EngineConfig.from_dict({"solver": {"mode": "balanced", "goal_color": 2}})
    path = tmp_path / "config.json"
    config.save(str(path))
    reloaded = EngineConfig.load(str(path))
    assert reloaded.solver.goal_color == 2
    assert reloaded.solver.mode == "balanced"

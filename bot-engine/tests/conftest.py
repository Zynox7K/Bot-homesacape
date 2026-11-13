"""Configuration partagée des tests du moteur."""

from __future__ import annotations

import os
import sys

ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ENGINE_ROOT not in sys.path:
    sys.path.insert(0, ENGINE_ROOT)

import pytest  # noqa: E402

from engine.config import EngineConfig  # noqa: E402
from engine.service import Engine  # noqa: E402


@pytest.fixture()
def config() -> EngineConfig:
    return EngineConfig.load(os.path.join(ENGINE_ROOT, "config.json"))


@pytest.fixture()
def engine(config: EngineConfig) -> Engine:
    return Engine(config)

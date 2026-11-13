"""Contrat réseau entre l'application Android et le moteur.

Toutes les coordonnées sont **normalisées** (0 à 1) relativement à l'image envoyée
par le client. L'application les multiplie par la largeur et la hauteur réelles de
son écran : aucun accord préalable sur la résolution n'est nécessaire.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class PointModel(BaseModel):
    x: float
    y: float


class RectModel(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class MoveModel(BaseModel):
    """Geste à exécuter côté client."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str = "swipe"  # swipe | tap
    source: PointModel = Field(alias="from")
    target: Optional[PointModel] = Field(default=None, alias="to")
    duration_ms: int = 180
    score: float = 0.0
    reason: str = ""
    combo: bool = False


class GridModel(BaseModel):
    rows: int
    cols: int
    confidence: float
    pitch: float
    origin: PointModel
    bounds: RectModel


class TimingModel(BaseModel):
    decode_ms: float = 0.0
    vision_ms: float = 0.0
    solver_ms: float = 0.0
    render_ms: float = 0.0
    total_ms: float = 0.0


class AnalyzeResponse(BaseModel):
    """Réponse complète d'une analyse."""

    ok: bool
    message: str = ""
    move: Optional[MoveModel] = None
    grid: Optional[GridModel] = None
    board: list[list[int]] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    background: str = "#000000"
    confidence: float = 0.0
    pieces: int = 0
    blocks: int = 0
    holes: int = 0
    specials: int = 0
    settle_ms: int = 1250
    capture_interval_ms: int = 1000
    stale: bool = False
    stale_count: int = 0
    timing: TimingModel = Field(default_factory=TimingModel)
    debug_image: Optional[str] = None
    engine_version: str = ""
    solver_mode: str = ""


class AnalyzeRequest(BaseModel):
    """Variante JSON de la requête (alternative à l'envoi multipart)."""

    image_base64: str
    session_id: str = ""
    display_width: int = 0
    display_height: int = 0
    debug: bool = False
    cell_sheet: bool = False
    goal_color: Optional[int] = None
    config: Optional[dict[str, Any]] = None


class HealthModel(BaseModel):
    status: str
    version: str
    uptime_s: float
    templates: list[str] = Field(default_factory=list)
    solver_mode: str = ""


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: str = ""

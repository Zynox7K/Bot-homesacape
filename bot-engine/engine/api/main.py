"""API HTTP du moteur.

Les points d'entrée utiles au quotidien :

* ``GET  /health``  — sonde de vie (utilisée par Render/Railway et par l'app) ;
* ``POST /analyze`` — capture d'écran en entrée, geste à jouer en sortie ;
* ``GET  /config`` / ``POST /config`` — lecture et synchronisation à chaud de la
  configuration versionnée sur le dépôt GitHub (écriture protégée par
  ``X-Engine-Key``) ;
* ``POST /config/reload`` — rechargement du fichier depuis le disque.

L'analyse est coûteuse en CPU : elle est systématiquement exécutée dans le pool de
threads pour ne jamais bloquer la boucle événementielle.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..protocol import AnalyzeRequest, AnalyzeResponse, ErrorResponse, HealthModel
from ..service import AnalysisRequest, Engine

ADMIN_KEY = os.environ.get("AUTOPLAY_ADMIN_KEY", "").strip()
CONFIG_PATH = os.environ.get("AUTOPLAY_CONFIG", "")

engine = Engine()

app = FastAPI(
    title="AutoPlay Engine",
    version=__version__,
    description=(
        "Moteur de reconnaissance de grille et de résolution match-3, "
        "appelé par le client Android AutoPlay."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------- #
# Sondes et configuration
# ---------------------------------------------------------------------- #
@app.get("/", response_model=HealthModel, include_in_schema=False)
@app.get("/health", response_model=HealthModel)
async def health() -> HealthModel:
    return HealthModel(
        status="ok",
        version=__version__,
        uptime_s=round(engine.uptime, 2),
        templates=engine.templates.names(),
        solver_mode=engine.config.solver.mode,
    )


@app.get("/config")
async def read_config() -> dict[str, Any]:
    """Configuration active, telle que l'application Android peut l'afficher."""
    return engine.config.to_dict()


@app.post("/config", response_model=dict, responses={403: {"model": ErrorResponse}})
async def write_config(
    payload: dict[str, Any],
    x_engine_key: str = Header(default=""),
) -> Any:
    """Applique une nouvelle configuration à chaud (synchronisation depuis GitHub).

    Protégée par la clé partagée ``AUTOPLAY_ADMIN_KEY``. Sans clé configurée côté
    serveur, l'écriture est refusée : le moteur reste alors en lecture seule.
    """
    if not ADMIN_KEY:
        raise HTTPException(
            status_code=403,
            detail="AUTOPLAY_ADMIN_KEY n'est pas défini sur le serveur : écriture refusée.",
        )
    if x_engine_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clé d'administration invalide.")

    engine.update_config(payload)
    if CONFIG_PATH:
        engine.config.save(CONFIG_PATH)
    return engine.config.to_dict()


@app.post("/config/reload")
async def reload_config(x_engine_key: str = Header(default="")) -> dict[str, Any]:
    """Recharge ``config.json`` depuis le disque (après un déploiement)."""
    if ADMIN_KEY and x_engine_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clé d'administration invalide.")
    engine.update_config(_load_disk_config())
    return engine.config.to_dict()


def _load_disk_config() -> dict[str, Any]:
    from ..config import EngineConfig

    return EngineConfig.load(CONFIG_PATH or None).to_dict()


# ---------------------------------------------------------------------- #
# Analyse
# ---------------------------------------------------------------------- #
@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def analyze(request: Request) -> Any:
    """Reçoit une capture d'écran et renvoie le geste à exécuter.

    Deux transports sont acceptés :

    * ``multipart/form-data`` avec le champ binaire ``image`` (recommandé : pas de
      surcoût base64, donc moins de bande passante et moins de latence) ;
    * ``application/json`` avec ``image_base64``.
    """
    content_type = request.headers.get("content-type", "")
    analysis = await _read_request(request, content_type)
    response = await run_in_threadpool(engine.analyze, analysis)
    return response


async def _read_request(request: Request, content_type: str) -> AnalysisRequest:
    if content_type.startswith("multipart/form-data"):
        return await _read_multipart(request)
    return await _read_json(request)


async def _read_multipart(request: Request) -> AnalysisRequest:
    try:
        form = await request.form()
    except Exception as error:  # pragma: no cover - dépend du client HTTP
        raise HTTPException(status_code=400, detail=f"Formulaire illisible : {error}")

    upload = form.get("image")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Champ binaire 'image' manquant.")
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Image vide.")

    overrides = _parse_json_field(form.get("config"))
    return AnalysisRequest(
        image=data,
        session_id=str(form.get("session_id") or ""),
        display_width=_as_int(form.get("display_width")),
        display_height=_as_int(form.get("display_height")),
        debug=_as_bool(form.get("debug")),
        cell_sheet=_as_bool(form.get("cell_sheet")),
        goal_color=_as_optional_int(form.get("goal_color")),
        overrides=overrides,
    )


async def _read_json(request: Request) -> AnalysisRequest:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")

    try:
        model = AnalyzeRequest.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(status_code=400, detail=f"requête invalide : {error.errors()[:2]}")
    try:
        data = base64.b64decode(model.image_base64, validate=False)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"base64 invalide : {error}")
    if not data:
        raise HTTPException(status_code=400, detail="Image vide après décodage.")

    return AnalysisRequest(
        image=data,
        session_id=model.session_id,
        display_width=model.display_width,
        display_height=model.display_height,
        debug=model.debug,
        cell_sheet=model.cell_sheet,
        goal_color=model.goal_color,
        overrides=model.config,
    )


def _parse_json_field(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None or isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "oui", "yes", "on"}


# ---------------------------------------------------------------------- #
# Erreurs uniformes : le client attend toujours un objet JSON exploitable.
# ---------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": str(exc.detail), "detail": request.url.path},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "erreur interne du moteur", "detail": str(exc)},
    )

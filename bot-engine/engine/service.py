"""Orchestration du moteur : image reçue → grille → meilleur coup → geste.

C'est le point d'entrée unique utilisé par l'API HTTP et par les tests. Le module
ne dépend d'aucune bibliothèque réseau : il reçoit des octets et renvoie un objet
``AnalyzeResponse`` sérialisable.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .config import EngineConfig
from .model import describe
from .protocol import (
    AnalyzeResponse,
    GridModel,
    MoveModel,
    PointModel,
    RectModel,
    TimingModel,
)
from .solver.board import Board
from .solver.search import MovePlan, best_move
from .vision.classifier import BoardScan, classify
from .vision.debug import encode_png_base64, render_cell_sheet, render_overlay
from .vision.frame import Frame, build_frame, decode_image
from .vision.grid import GridLayout, locate_grid
from .vision.templates import build_library

MESSAGE_NO_BOARD = "Grille non détectée : ouvrez un niveau, cadrez le plateau, puis réessayez."
MESSAGE_LOW_CONFIDENCE = "Plateau reconnu avec une fiabilité insuffisante."
MESSAGE_NO_MOVE = "Aucun échange légal : le jeu va redistribuer les pièces."
MESSAGE_DECODE_ERROR = "Image illisible."
MESSAGE_STALE = "Plateau inchangé depuis la dernière analyse."

#: Nombre de sessions simultanées suivies pour la détection de plateau figé.
MAX_SESSIONS = 64


@dataclass
class AnalysisRequest:
    """Requête d'analyse normalisée (indépendante du transport HTTP)."""

    image: bytes
    session_id: str = ""
    display_width: int = 0
    display_height: int = 0
    debug: bool = False
    cell_sheet: bool = False
    goal_color: int | None = None
    overrides: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Engine:
    """Moteur sans état partagé : chaque analyse est autonome et reproductible."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = (config or EngineConfig.load()).apply_mode()
        self.templates = build_library(
            self.config.vision.template_dir,
            self.config.vision.template_size,
            self.config.vision.template_threshold,
        )
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._sessions: "OrderedDict[str, tuple[tuple, int]]" = OrderedDict()

    # ------------------------------------------------------------------ #
    def track_session(self, session_id: str, board: Board) -> tuple[bool, int]:
        """Signale un plateau identique à l'analyse précédente de la session.

        Permet au client de distinguer « le geste n'a pas été pris en compte »
        d'un vrai coup à jouer, et d'allonger la temporisation au lieu de renvoyer
        des captures à l'identique.
        """
        if not session_id:
            return False, 0
        signature = board.signature()
        with self._lock:
            previous = self._sessions.get(session_id)
            count = previous[1] + 1 if previous and previous[0] == signature else 0
            self._sessions[session_id] = (signature, count)
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        return count >= 1, count

    # ------------------------------------------------------------------ #
    def update_config(self, payload: dict[str, Any] | None) -> EngineConfig:
        """Remplace la configuration active (synchronisation depuis le dépôt)."""
        self.config = EngineConfig.from_dict(payload or {}).apply_mode()
        self.templates = build_library(
            self.config.vision.template_dir,
            self.config.vision.template_size,
            self.config.vision.template_threshold,
        )
        return self.config

    def apply_overrides(self, payload: dict[str, Any] | None) -> EngineConfig:
        return self.config.with_overrides(payload)

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    # ------------------------------------------------------------------ #
    def analyze(self, request: AnalysisRequest) -> AnalyzeResponse:
        started = time.perf_counter()
        timing = TimingModel()

        config = self.apply_overrides(request.overrides)
        if request.goal_color is not None and request.goal_color >= 0:
            config.solver.goal_color = int(request.goal_color)

        # ---------------------------------------------------------- décodage
        try:
            image = decode_image(request.image)
        except ValueError as error:
            timing.total_ms = _ms(started)
            return AnalyzeResponse(
                ok=False,
                message=f"{MESSAGE_DECODE_ERROR} ({error})",
                timing=timing,
                engine_version=__version__,
                solver_mode=config.solver.mode,
            )
        timing.decode_ms = _ms(started)

        # ---------------------------------------------------------- vision
        vision_started = time.perf_counter()
        frame = build_frame(
            image,
            config.vision.analysis_width,
            config.vision.min_saturation,
            config.vision.min_value,
        )
        grid = locate_grid(frame, config.vision)
        if grid is None:
            timing.vision_ms = _ms(vision_started)
            timing.total_ms = _ms(started)
            return AnalyzeResponse(
                ok=False,
                message=MESSAGE_NO_BOARD,
                confidence=0.0,
                settle_ms=config.default_settle_ms,
                capture_interval_ms=config.min_capture_interval_ms,
                timing=timing,
                engine_version=__version__,
                solver_mode=config.solver.mode,
            )

        scan = classify(frame, grid, config.vision, self.templates)
        timing.vision_ms = _ms(vision_started)

        # ---------------------------------------------------------- résolution
        solver_started = time.perf_counter()
        board = Board.from_matrix(scan.values, palette_size=max(1, scan.color_count))
        plan = best_move(board, config.solver)
        timing.solver_ms = _ms(solver_started)

        stale, stale_count = self.track_session(request.session_id, board)

        # ---------------------------------------------------------- réponse
        move = _build_move(plan, scan.grid, frame, config) if plan else None
        message = ""
        if scan.confidence < 0.28:
            message = MESSAGE_LOW_CONFIDENCE
        elif plan is None:
            message = MESSAGE_NO_MOVE
        elif stale:
            message = MESSAGE_STALE

        debug_image = None
        if request.debug or request.cell_sheet:
            render_started = time.perf_counter()
            if request.cell_sheet:
                debug_image = encode_png_base64(render_cell_sheet(frame, scan))
            else:
                lines = _overlay_lines(scan, plan, timing, config)
                debug_image = encode_png_base64(render_overlay(frame, scan, plan, lines))
            timing.render_ms = _ms(render_started)

        timing.total_ms = _ms(started)

        return AnalyzeResponse(
            ok=True,
            message=message,
            move=move,
            grid=_build_grid(scan.grid, frame),
            board=scan.values,
            palette=scan.palette_hex,
            background=scan.background_hex,
            confidence=round(scan.confidence, 4),
            pieces=_count_pieces(scan),
            blocks=scan.block_count,
            holes=scan.hole_count,
            specials=scan.special_count,
            settle_ms=config.default_settle_ms,
            capture_interval_ms=config.min_capture_interval_ms,
            stale=stale,
            stale_count=stale_count,
            timing=timing,
            debug_image=debug_image,
            engine_version=__version__,
            solver_mode=config.solver.mode,
        )


# ---------------------------------------------------------------------- #
def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


def _count_pieces(scan: BoardScan) -> int:
    return sum(1 for row in scan.values for value in row if value >= 0)


def _build_move(
    plan: MovePlan, grid: GridLayout, frame: Frame, config: EngineConfig
) -> MoveModel:
    source_row, source_col = divmod(plan.from_index, grid.cols)
    target_row, target_col = divmod(plan.to_index, grid.cols)
    source_x, source_y = grid.center(source_row, source_col)
    target_x, target_y = grid.center(target_row, target_col)

    normalized_source = frame.to_normalized(source_x, source_y)
    normalized_target = frame.to_normalized(target_x, target_y)

    return MoveModel(
        kind="swipe",
        source=PointModel(x=round(normalized_source[0], 6), y=round(normalized_source[1], 6)),
        target=PointModel(x=round(normalized_target[0], 6), y=round(normalized_target[1], 6)),
        duration_ms=int(config.default_move_duration_ms),
        score=round(plan.score, 3),
        reason=plan.reason,
        combo=plan.combo,
    )


def _build_grid(grid: GridLayout, frame: Frame) -> GridModel:
    left, top = frame.to_normalized(grid.left, grid.top)
    right, bottom = frame.to_normalized(
        grid.left + (grid.cols - 1) * grid.pitch, grid.top + (grid.rows - 1) * grid.pitch
    )
    board_left, board_top = frame.to_normalized(grid.board_left, grid.board_top)
    board_right, board_bottom = frame.to_normalized(grid.board_right, grid.board_bottom)
    return GridModel(
        rows=grid.rows,
        cols=grid.cols,
        confidence=round(grid.confidence, 4),
        pitch=round(grid.pitch / max(1, frame.width), 6),
        origin=PointModel(x=round(left, 6), y=round(top, 6)),
        bounds=RectModel(
            left=round(board_left, 6),
            top=round(board_top, 6),
            right=round(board_right, 6),
            bottom=round(board_bottom, 6),
        ),
    )


def _overlay_lines(
    scan: BoardScan, plan: MovePlan | None, timing: TimingModel, config: EngineConfig
) -> list[str]:
    lines = [
        "AutoPlay engine {}".format(__version__),
        "grille {} x {}  fiabilite {:.2f}".format(
            scan.grid.rows, scan.grid.cols, scan.confidence
        ),
        "couleurs {}  obstacles {}  trous {}".format(
            scan.color_count, scan.block_count, scan.hole_count
        ),
        "vision {:.0f} ms  solveur {:.0f} ms  mode {}".format(
            timing.vision_ms, timing.solver_ms, config.solver.mode
        ),
    ]
    if plan is not None:
        source_row, source_col = divmod(plan.from_index, scan.grid.cols)
        target_row, target_col = divmod(plan.to_index, scan.grid.cols)
        lines.append(
            "coup L{}C{} -> L{}C{}  score {:.1f}".format(
                source_row + 1, source_col + 1, target_row + 1, target_col + 1, plan.score
            )
        )
        lines.append(plan.reason)
        if plan.cleared_preview:
            preview = ", ".join(describe(scan.flat_values()[index]) for index in plan.cleared_preview[:4])
            lines.append("libere : {}".format(preview))
    else:
        lines.append(MESSAGE_NO_MOVE)
    return lines

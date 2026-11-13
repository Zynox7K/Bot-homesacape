"""Analyse une capture d'écran depuis un fichier, sans serveur ni téléphone.

C'est l'outil de mise au point : on récupère une capture du niveau
(``adb exec-out screencap -p > capture.png``), on lance l'analyse, et l'on obtient
le coup retenu, la grille détectée et — avec ``--debug`` — l'image annotée.

Exemples
--------
    python tools/analyze_file.py capture.png
    python tools/analyze_file.py capture.png --debug annotated.png --mode expert
    python tools/analyze_file.py capture.png --goal-color 2 --json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import EngineConfig, MODE_PRESETS  # noqa: E402
from engine.model import BLOCK, HOLE, describe, is_special  # noqa: E402
from engine.service import AnalysisRequest, Engine  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse une capture d'écran et affiche le meilleur coup."
    )
    parser.add_argument("image", help="chemin de la capture (PNG, JPG, WebP)")
    parser.add_argument("--config", help="chemin d'un config.json alternatif")
    parser.add_argument("--mode", choices=sorted(MODE_PRESETS), help="profondeur de calcul")
    parser.add_argument("--goal-color", type=int, default=None, help="indice de couleur prioritaire")
    parser.add_argument("--debug", metavar="FICHIER", help="enregistre l'image annotée")
    parser.add_argument(
        "--cell-sheet",
        metavar="FICHIER",
        help="enregistre la planche des cases (remplace --debug si les deux sont donnés)",
    )
    parser.add_argument("--json", action="store_true", help="sortie JSON brute")
    parser.add_argument("--show-board", action="store_true", help="affiche la grille détectée")
    return parser


def render_board(values: list[list[int]], palette: list[str]) -> str:
    lines = []
    for row in values:
        cells = []
        for value in row:
            if value == HOLE:
                cells.append("  . ")
            elif value == BLOCK:
                cells.append("  # ")
            elif is_special(value):
                cells.append(f" {value - 100:3d}")
            else:
                cells.append(f"  {value} ")
        lines.append("".join(cells))
    header = "    " + "".join(f"{index:^4d}" for index in range(len(values[0]) if values else 0))
    return header + "\n" + "\n".join(lines) + "\n    palette : " + ", ".join(
        f"{index}={color}" for index, color in enumerate(palette)
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.image):
        print(f"capture introuvable : {args.image}", file=sys.stderr)
        return 2
    with open(args.image, "rb") as handle:
        payload = handle.read()

    config = EngineConfig.load(args.config)
    if args.mode:
        config.solver.mode = args.mode
        config.solver_explicit_fields.discard("mode")
        config.apply_mode()
    engine = Engine(config)

    response = engine.analyze(
        AnalysisRequest(
            image=payload,
            debug=bool(args.debug),
            cell_sheet=bool(args.cell_sheet),
            goal_color=args.goal_color,
        )
    )

    if args.json:
        print(json.dumps(response.model_dump(by_alias=True), indent=2, ensure_ascii=False, default=str))
    else:
        print(f"état            : {'succès' if response.ok else 'échec'}")
        if response.message:
            print(f"message         : {response.message}")
        if response.grid is not None:
            print(
                "grille          : {} × {}  pas={:.4f}  origine=({:.4f} ; {:.4f})".format(
                    response.grid.rows,
                    response.grid.cols,
                    response.grid.pitch,
                    response.grid.origin.x,
                    response.grid.origin.y,
                )
            )
            print(
                "fiabilité       : {:.3f} (grille {:.3f})".format(
                    response.confidence, response.grid.confidence
                )
            )
            print(
                "contenu         : {} pièces, {} obstacle(s), {} trou(s), {} bonus".format(
                    response.pieces, response.blocks, response.holes, response.specials
                )
            )
        if response.move is not None:
            print(
                "coup            : ({:.4f} ; {:.4f}) -> ({:.4f} ; {:.4f})".format(
                    response.move.source.x,
                    response.move.source.y,
                    response.move.target.x if response.move.target else 0.0,
                    response.move.target.y if response.move.target else 0.0,
                )
            )
            print(f"score           : {response.move.score:.2f}")
            print(f"explication     : {response.move.reason}")
        timing = response.timing
        print(
            "temps           : décodage {:.0f} ms · vision {:.0f} ms · solveur {:.0f} ms · total {:.0f} ms".format(
                timing.decode_ms, timing.vision_ms, timing.solver_ms, timing.total_ms
            )
        )
        if args.show_board and response.board:
            print(render_board(response.board, response.palette))

    # Le protocole ne transporte qu'une seule image de débogage : la planche des
    # cases est prioritaire sur l'image annotée, comme côté serveur.
    if args.cell_sheet or args.debug:
        flag = "planche des cases" if args.cell_sheet else "image annotée"
        path = args.cell_sheet or args.debug
        if not response.debug_image:
            print(f"aucune {flag} produite", file=sys.stderr)
        else:
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(response.debug_image))
            print(f"{flag} écrite : {path}")
            if args.cell_sheet and args.debug:
                print("note : --cell-sheet prime sur --debug", file=sys.stderr)

    if response.board and not args.json:
        sample = [describe(value) for value in response.board[0][:3]]
        print(f"échantillon     : {', '.join(sample)}")

    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

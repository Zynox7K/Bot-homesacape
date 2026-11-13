"""Analyse une capture d'écran, localement ou via un moteur déployé.

C'est l'outil de mise au point et de vérification : on récupère une capture du
niveau (``adb exec-out screencap -p > capture.png``), on lance l'analyse, et l'on
obtient le coup retenu, la grille détectée et — avec ``--debug`` — l'image
annotée.

Sans ``--url``, l'analyse s'exécute ici même, dans le processus : c'est le mode de
travail sur la vision et le solveur. Avec ``--url``, la capture est envoyée au
service déployé exactement comme le fait le téléphone : c'est le mode de contrôle
d'un déploiement.

Exemples
--------
    python tools/analyze_file.py capture.png
    python tools/analyze_file.py capture.png --show-board --debug annotated.png
    python tools/analyze_file.py capture.png --mode expert --json
    python tools/analyze_file.py capture.png --url https://autoplay-engine.onrender.com
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import MODE_PRESETS, EngineConfig  # noqa: E402
from engine.model import BLOCK, HOLE, describe, is_special  # noqa: E402
from engine.service import AnalysisRequest, Engine  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse une capture d'écran et affiche le meilleur coup."
    )
    parser.add_argument("image", help="chemin de la capture (PNG, JPG, WebP)")
    parser.add_argument(
        "--url",
        help="moteur distant à interroger (défaut : analyse locale dans ce processus)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="délai d'attente réseau, en secondes")
    parser.add_argument("--config", help="chemin d'un config.json alternatif (analyse locale)")
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


# ---------------------------------------------------------------------- #
# Transports
# ---------------------------------------------------------------------- #
def analyze_local(path: str, args: argparse.Namespace) -> dict:
    with open(path, "rb") as handle:
        payload = handle.read()

    config = EngineConfig.load(args.config)
    engine = Engine(config)
    overrides: dict = {}
    if args.mode:
        overrides["solver"] = {"mode": args.mode}

    response = engine.analyze(
        AnalysisRequest(
            image=payload,
            debug=bool(args.debug),
            cell_sheet=bool(args.cell_sheet),
            goal_color=args.goal_color,
            overrides=overrides or None,
        )
    )
    return response.model_dump(by_alias=True)


def analyze_remote(url: str, path: str, args: argparse.Namespace) -> dict:
    """Envoie la capture en multipart, comme le fait l'application Android."""
    with open(path, "rb") as handle:
        image = handle.read()

    fields: list[tuple[str, str]] = [("session_id", "analyze-file")]
    if args.goal_color is not None:
        fields.append(("goal_color", str(args.goal_color)))
    config: dict = {}
    if args.mode:
        config["solver"] = {"mode": args.mode}
    if config:
        fields.append(("config", json.dumps(config)))
    if args.debug:
        fields.append(("debug", "true"))
    if args.cell_sheet:
        fields.append(("cell_sheet", "true"))

    boundary = f"analyze-file-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields:
        body += f"--{boundary}\r\n".encode("ascii")
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
        body += value.encode("utf-8") + b"\r\n"
    body += f"--{boundary}\r\n".encode("ascii")
    body += (
        f'Content-Disposition: form-data; name="image"; '
        f'filename="{os.path.basename(path)}"\r\n'
    ).encode("ascii")
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += image
    body += f"\r\n--{boundary}--\r\n".encode("ascii")

    request = urllib.request.Request(
        url.rstrip("/") + "/analyze",
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "autoplay-analyze-file/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {error.code} du moteur : {detail[:400]}")
    except urllib.error.URLError as error:
        raise SystemExit(f"moteur injoignable ({url}) : {error.reason}")


# ---------------------------------------------------------------------- #
# Rendu
# ---------------------------------------------------------------------- #
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
    columns = len(values[0]) if values else 0
    header = "    " + "".join(f"{index:^4d}" for index in range(columns))
    legend = ", ".join(f"{index}={color}" for index, color in enumerate(palette))
    return header + "\n" + "\n".join(lines) + "\n    palette : " + legend


def render_summary(data: dict, source: str) -> None:
    grid = data.get("grid") or {}
    move = data.get("move") or {}
    timing = data.get("timing") or {}
    origin = grid.get("origin") or {}

    print(f"source          : {source}")
    print(f"état            : {'succès' if data.get('ok') else 'échec'}")
    if data.get("message"):
        print(f"message         : {data['message']}")
    if grid:
        print(f"grille          : {grid.get('rows')} × {grid.get('cols')}"
              f"  pas={grid.get('pitch', 0):.4f}"
              f"  origine=({origin.get('x', 0):.4f} ; {origin.get('y', 0):.4f})")
        print(f"fiabilité       : {data.get('confidence', 0):.3f}"
              f" (grille {grid.get('confidence', 0):.3f})")
        print("contenu         : {} pièces, {} obstacle(s), {} trou(s), {} bonus".format(
            data.get("pieces", 0), data.get("blocks", 0), data.get("holes", 0), data.get("specials", 0),
        ))
    if move:
        source_point = move.get("from") or {}
        target_point = move.get("to") or {}
        print("coup            : ({:.4f} ; {:.4f}) -> ({:.4f} ; {:.4f})".format(
            source_point.get("x", 0.0), source_point.get("y", 0.0),
            target_point.get("x", 0.0), target_point.get("y", 0.0),
        ))
        print(f"score           : {move.get('score', 0.0):.2f}")
        print(f"explication     : {move.get('reason', '')}")
    if timing:
        print("temps           : décodage {:.0f} ms · vision {:.0f} ms · solveur {:.0f} ms · total {:.0f} ms".format(
            timing.get("decode_ms", 0.0), timing.get("vision_ms", 0.0),
            timing.get("solver_ms", 0.0), timing.get("total_ms", 0.0),
        ))
    if data.get("stale"):
        print(f"obsolescence    : plateau inchangé ({data.get('stale_count', 0)} fois)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.image):
        print(f"capture introuvable : {args.image}", file=sys.stderr)
        return 2

    if args.url:
        data = analyze_remote(args.url, args.image, args)
        source = args.url.rstrip("/") + "/analyze"
    else:
        data = analyze_local(args.image, args)
        source = "moteur local (ce processus)"

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        render_summary(data, source)
        if args.show_board and data.get("board"):
            print(render_board(data["board"], data.get("palette") or []))

    # Le protocole ne transporte qu'une seule image de débogage : la planche des
    # cases est prioritaire sur l'image annotée, comme côté serveur.
    if args.cell_sheet or args.debug:
        label = "planche des cases" if args.cell_sheet else "image annotée"
        path = args.cell_sheet or args.debug
        image = data.get("debug_image")
        if not image:
            print(f"aucune {label} produite", file=sys.stderr)
        else:
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(image))
            print(f"{label} écrite : {path}")
            if args.cell_sheet and args.debug:
                print("note : --cell-sheet prime sur --debug", file=sys.stderr)

    board = data.get("board")
    if board and not args.json:
        sample = [describe(value) for value in board[0][:3]]
        print(f"échantillon     : {', '.join(sample)}")

    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

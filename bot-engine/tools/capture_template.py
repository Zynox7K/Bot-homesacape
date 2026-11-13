"""Extrait une case d'une capture et l'enregistre comme gabarit de bonus.

Le gabarit produit est directement utilisable par le moteur : il suffit de le
pousser dans ``bot-engine/templates/`` puis de redéployer.

Exemple
-------
    python tools/capture_template.py capture.png --row 4 --col 2 --kind bomb
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from engine.config import EngineConfig  # noqa: E402
from engine.vision.classifier import cell_crop  # noqa: E402
from engine.vision.frame import build_frame, decode_image  # noqa: E402
from engine.vision.grid import locate_grid  # noqa: E402

VALID_KINDS = (
    "rocket_row",
    "rocket_column",
    "plane",
    "bomb",
    "disco",
    "booster",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crée un gabarit de bonus depuis une capture.")
    parser.add_argument("image", help="capture d'écran contenant le bonus")
    parser.add_argument("--row", type=int, required=True, help="ligne de la case (0 = première)")
    parser.add_argument("--col", type=int, required=True, help="colonne de la case (0 = première)")
    parser.add_argument("--kind", required=True, choices=VALID_KINDS, help="type de bonus")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
        help="dossier de destination (défaut : bot-engine/templates)",
    )
    parser.add_argument("--config", help="chemin d'un config.json alternatif")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = EngineConfig.load(args.config).vision

    with open(args.image, "rb") as handle:
        image = decode_image(handle.read())

    frame = build_frame(image, config.analysis_width, config.min_saturation, config.min_value)
    grid = locate_grid(frame, config)
    if grid is None:
        print("grille non détectée dans cette capture", file=sys.stderr)
        return 1

    if not (0 <= args.row < grid.rows and 0 <= args.col < grid.cols):
        print(
            f"case hors grille : la détection donne {grid.rows} × {grid.cols}",
            file=sys.stderr,
        )
        return 1

    crop = cell_crop(frame, grid, args.row, args.col)
    if crop is None:
        print("case trop proche du bord pour être extraite", file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)
    destination = os.path.join(args.out, f"{args.kind}.png")
    if not cv2.imwrite(destination, crop):
        print(f"écriture impossible : {destination}", file=sys.stderr)
        return 1

    print(
        "grille {} × {} (fiabilité {:.2f}) — gabarit {} écrit : {} ({} × {} px)".format(
            grid.rows, grid.cols, grid.confidence, args.kind, destination, crop.shape[1], crop.shape[0]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

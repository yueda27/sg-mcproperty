"""`sg-property-load <dataset> <path>` entry point."""

import argparse
from pathlib import Path

from . import hdb_resale

#LOADERS = {"hdb-resale": hdb_resale.load}  # register new loaders here


def main() -> None:
    parser = argparse.ArgumentParser(prog="sg-property-load")
    parser.add_argument("dataset", choices=sorted(LOADERS))
    parser.add_argument("path", type=Path, help="source file, typically under data/")
    args = parser.parse_args()
    rows = LOADERS[args.dataset](args.path)
    print(f"Loaded {rows} rows from {args.path}")

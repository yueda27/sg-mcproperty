"""`sg-property-load <dataset> [--dump]` entry point."""

import argparse

from sg_mcproperty.loaders.utils import dump_file

from . import private_residential

LOADERS = {
    "private-residential": {
        "function": private_residential.load,
        "filename": "private_residential.parquet",
    }
}


def run_loader(loader_func, file_name: str, write_file: bool = False):
    df = loader_func()
    if write_file:
        dump_file(df, file_name)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(prog="sg-property-load")
    parser.add_argument("dataset", choices=sorted(LOADERS))
    parser.add_argument(
        "--dump", action="store_true", help="write the result to output/ as parquet"
    )
    args = parser.parse_args()
    loader = LOADERS[args.dataset]
    df = run_loader(loader["function"], loader["filename"], args.dump)
    print(f"Loaded {df.height} rows for {args.dataset}")


if __name__ == "__main__":
    main()

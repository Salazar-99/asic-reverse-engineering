#!/usr/bin/env python3
"""Elaborate the recovered netlist against SKY130 HD functional models."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = Path(__file__).resolve().parent
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
DEFAULT_LIBRARY_ROOT = Path.home() / "src" / "skywater-pdk-libs-sky130_fd_sc_hd"
DEFAULT_NETLIST = OUTPUTS_ROOT / "puzzle_extracted.v"
DEFAULT_TESTBENCH = SCRIPTS_ROOT / "tb_puzzle.v"
WRAPPER_PATTERN = re.compile(r"_\d+\.v$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library-root",
        type=Path,
        default=Path(os.environ.get("SKY130_HD_ROOT", DEFAULT_LIBRARY_ROOT)),
        help="Checkout of google/skywater-pdk-libs-sky130_fd_sc_hd",
    )
    parser.add_argument(
        "--netlist",
        type=Path,
        default=DEFAULT_NETLIST,
        help="Structural Verilog netlist to elaborate",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output Icarus VVP file",
    )
    parser.add_argument(
        "--testbench",
        type=Path,
        nargs="?",
        const=DEFAULT_TESTBENCH,
        default=None,
        help="Optional testbench (defaults to scripts/tb_puzzle.v when flag is given alone)",
    )
    parser.add_argument(
        "--top",
        help="Top module to elaborate (defaults to puzzle or tb_puzzle)",
    )
    args = parser.parse_args()

    if shutil.which("iverilog") is None:
        raise SystemExit("iverilog is not on PATH; install it with `brew install icarus-verilog`.")
    if not args.netlist.is_file():
        raise SystemExit(f"Netlist does not exist: {args.netlist}")
    if args.testbench is not None and not args.testbench.is_file():
        raise SystemExit(f"Testbench does not exist: {args.testbench}")

    cells = args.library_root / "cells"
    if not cells.is_dir():
        raise SystemExit(
            f"SKY130 HD model checkout does not exist: {args.library_root}\n"
            "Clone https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd "
            "or set SKY130_HD_ROOT."
        )

    # Size-specific wrappers provide modules such as nand2_2.  Each wrapper
    # includes its base cell's FUNCTIONAL implementation from its own folder.
    wrappers = sorted(path for path in cells.rglob("*.v") if WRAPPER_PATTERN.search(path.name))
    include_dirs = sorted({path.parent for path in cells.rglob("*.v")})
    if not wrappers:
        raise SystemExit(f"No SKY130 cell wrappers found below {cells}")

    top = args.top or ("tb_puzzle" if args.testbench else "puzzle")
    output = args.output or OUTPUTS_ROOT / f"{top}.vvp"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "iverilog",
        "-g2012",
        "-DFUNCTIONAL",
        "-DUSE_POWER_PINS",
        "-s",
        top,
        "-o",
        str(output),
        *(f"-I{directory}" for directory in include_dirs),
        str(args.netlist),
        *([str(args.testbench)] if args.testbench else []),
        *(str(wrapper) for wrapper in wrappers),
    ]
    subprocess.run(command, check=True)
    print(f"Elaborated {args.netlist.name} with {len(wrappers)} SKY130 HD wrappers: {output}")


if __name__ == "__main__":
    main()

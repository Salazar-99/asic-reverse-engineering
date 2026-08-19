# asic-reverse-engineering

Extract a gate-level Verilog netlist from `puzzle.gds` and elaborate it against SKY130 HD functional models.

## Layout

```
puzzle.gds          # input GDS
scripts/            # extraction and elaboration tooling
outputs/            # generated netlists and simulation binaries
```

## Usage

```bash
make netlist   # puzzle.gds -> outputs/puzzle_extracted.v
make compile   # elaborate DUT -> outputs/puzzle.vvp
make test      # compile testbench and run smoke simulation
make all       # same as make test
```

Or run the scripts directly:

```bash
uv run python scripts/main.py
uv run python scripts/compile_netlist.py
uv run python scripts/compile_netlist.py --testbench
vvp outputs/tb_puzzle.vvp
```

Generate a directed component-dependency graph as Graphviz DOT:

```bash
uv run python scripts/graph_netlist.py
```

The default output is `outputs/puzzle_graph.dot`. Use an `.svg`, `.png`, or
`.pdf` output extension to render it directly when Graphviz is installed:

```bash
uv run python scripts/graph_netlist.py -o outputs/puzzle_graph.svg
uv run python scripts/graph_netlist.py --focus success --direction fanin --depth 6 \
  -o outputs/success_fanin.svg
```

To trace every data path from an input through combinational gates and
successive flip-flops, while hiding clock/reset wiring and opaque net labels,
use data-path mode. Flip-flops at the same sequential depth are aligned into
vertical "waves":

```bash
uv run python scripts/graph_netlist.py --data-paths-from I \
  -o outputs/I_data_paths.svg
```

Use `--data-paths-to` to trace the same register-aligned data flow backward
from an output:

```bash
uv run python scripts/graph_netlist.py --data-paths-to success \
  -o outputs/success_data_paths.svg
```

Power and physical-only cells are omitted by default. Run the script with
`--help` for filtering and display options. Component labels use friendly
logic names such as `OR (4-input)` by default; exact instance and SKY130 cell
names remain available as SVG hover tooltips. Use `--show-instance-names` or
`--raw-cell-names` to display them directly.

## Dependencies

### Dev container (recommended)

The easiest way to get started is to open this repo in a [Dev Container](https://code.visualstudio.com/docs/devcontainers/containers) (VS Code or Cursor: **Dev Containers: Reopen in Container**). The image in `.devcontainer/` pre-installs:

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- [KLayout](https://www.klayout.de/) for GDS netlist extraction
- [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`iverilog`, `vvp`) for elaboration and simulation
- [skywater-pdk-libs-sky130_fd_sc_hd](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd) at `/opt/skywater-pdk-libs-sky130_fd_sc_hd` (`SKY130_HD_ROOT`)
- Magic, Netgen, and a built SKY130A PDK (useful for broader layout/PDK work)

On first open, `uv sync` installs the Python dependencies from `pyproject.toml`. Then run:

```bash
make all
```

### Required software

| Tool | Required for | Notes |
|------|--------------|-------|
| Python 3.13+ | all scripts | Managed in the dev container |
| [uv](https://docs.astral.sh/uv/) | Python env / `make` | Runs `uv sync` on container create |
| [KLayout](https://www.klayout.de/) | `make netlist` | Reads `puzzle.gds`; no separate PDK needed for extraction |
| [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`iverilog`, `vvp`) | `make compile`, `make test` | Gate-level elaboration and simulation |
| [skywater-pdk-libs-sky130_fd_sc_hd](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd) | `make compile`, `make test` | SKY130 HD functional cell models |

Netlist extraction only needs KLayout and Python. The full PDK built in the dev container is not required for `make netlist` because `puzzle.gds` embeds the standard cells and pin labels.

### Manual setup

If you are not using the dev container, install the tools above on your machine.

**Python environment**

```bash
uv sync
```

**Icarus Verilog**

```bash
# macOS
brew install icarus-verilog

# Debian / Ubuntu
sudo apt install iverilog
```

**SKY130 HD cell library**

```bash
git clone https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd ~/src/skywater-pdk-libs-sky130_fd_sc_hd
```

If the library is checked out elsewhere, point `scripts/compile_netlist.py` at it:

```bash
export SKY130_HD_ROOT=/path/to/skywater-pdk-libs-sky130_fd_sc_hd
make compile
```

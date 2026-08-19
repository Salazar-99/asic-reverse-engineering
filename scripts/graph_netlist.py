#!/usr/bin/env python3
"""Build a directed component graph from a structural Verilog netlist."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NETLIST = PROJECT_ROOT / "outputs" / "puzzle_extracted.v"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "puzzle_graph.dot"

POWER_PINS = {"VGND", "VNB", "VPB", "VPWR", "VCCD1", "VCCD2", "VSSD1", "VSSD2"}
POWER_NETS = {"VGND", "VPWR", "VDD", "VSS", "1'b0", "1'b1", "1'h0", "1'h1"}
CONTROL_PINS = {"CLK", "GATE", "RESET", "RESET_B", "SET", "SET_B"}
OUTPUT_PINS = {
    "CO",
    "COUT",
    "GCLK",
    "HI",
    "LO",
    "Q",
    "Q_N",
    "SUM",
    "SUMOUT",
    "X",
    "Y",
    "Z",
}
PHYSICAL_CELL_MARKERS = ("__decap_", "__diode_", "__fill_", "__tap")
SEQUENTIAL_CELL_MARKERS = ("__df", "__dl", "__sdf")
SIMPLE_GATE_RE = re.compile(r"^(and|nand|or|nor|xor|xnor)(\d+)(?:b+)?$")
COMPOUND_GATE_RE = re.compile(r"^([ao])(\d+)(?:b+)?([ao])(i?)$")
INSTANCE_RE = re.compile(
    r"(?ms)^\s*([A-Za-z_\\][\w$\\.]*)\s+([A-Za-z_\\][\w$\\.]*)\s*\((.*?)\)\s*;"
)
CONNECTION_RE = re.compile(r"\.([A-Za-z_]\w*)\s*\(\s*([^)]+?)\s*\)")
PORT_DECL_RE = re.compile(
    r"(?m)^\s*(?:input|output|inout)\s*(?:wire\s+|logic\s+|reg\s+)?"
    r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([^;]+);"
)


@dataclass(frozen=True)
class Cell:
    cell_type: str
    instance: str
    connections: tuple[tuple[str, str], ...]

    @property
    def node(self) -> str:
        return f"cell/{self.instance}"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    net: str
    source_pin: str
    target_pin: str


def is_sequential(cell: Cell) -> bool:
    return any(marker in cell.cell_type for marker in SEQUENTIAL_CELL_MARKERS)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*?$", "", text, flags=re.M)


def parse_ports(text: str) -> set[str]:
    ports: set[str] = set()
    for match in PORT_DECL_RE.finditer(text):
        msb, lsb, names = match.groups()
        for name in names.split(","):
            name = name.strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                continue
            if msb is None:
                ports.add(name)
            else:
                start, stop = int(msb), int(lsb)
                step = 1 if stop >= start else -1
                ports.update(f"{name}[{index}]" for index in range(start, stop + step, step))
    return ports


def parse_cells(text: str, include_physical: bool) -> tuple[list[Cell], int]:
    cells: list[Cell] = []
    skipped = 0
    for match in INSTANCE_RE.finditer(text):
        cell_type, instance, body = match.groups()
        if cell_type in {"module", "assign"} or "." not in body:
            continue
        if not include_physical and any(marker in cell_type for marker in PHYSICAL_CELL_MARKERS):
            skipped += 1
            continue
        connections = tuple(
            (pin, re.sub(r"\s+", "", net)) for pin, net in CONNECTION_RE.findall(body)
        )
        if connections:
            cells.append(Cell(cell_type, instance, connections))
    return cells, skipped


def build_graph(
    cells: list[Cell], ports: set[str], include_power: bool
) -> tuple[dict[str, Cell], set[str], list[Edge]]:
    drivers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sinks: dict[str, list[tuple[str, str]]] = defaultdict(list)
    cell_by_node = {cell.node: cell for cell in cells}

    for cell in cells:
        for pin, net in cell.connections:
            if not include_power and (pin in POWER_PINS or net in POWER_NETS):
                continue
            endpoint = (cell.node, pin)
            if pin in OUTPUT_PINS:
                drivers[net].append(endpoint)
            else:
                sinks[net].append(endpoint)

    connected_ports: set[str] = set()
    edges: list[Edge] = []
    for net in sorted(set(drivers) | set(sinks)):
        net_drivers = drivers[net]
        net_sinks = sinks[net]
        if net in ports:
            port_node = f"port/{net}"
            connected_ports.add(net)
            if net_sinks and not net_drivers:
                net_drivers = [(port_node, "OUT")]
            elif net_drivers and not net_sinks:
                net_sinks = [(port_node, "IN")]
            else:
                # GDS ports are declared inout. If a port is both read and driven,
                # represent both directions instead of guessing its intended mode.
                if net_sinks:
                    net_drivers = [*net_drivers, (port_node, "OUT")]
                if drivers[net]:
                    net_sinks = [*net_sinks, (port_node, "IN")]
        for source, source_pin in net_drivers:
            for target, target_pin in net_sinks:
                if source != target:
                    edges.append(Edge(source, target, net, source_pin, target_pin))

    return cell_by_node, connected_ports, edges


def select_nodes(
    focus: list[str], depth: int | None, direction: str, all_nodes: set[str], edges: list[Edge]
) -> set[str]:
    if not focus:
        return all_nodes

    selected = {
        node
        for node in all_nodes
        if any(node.removeprefix("cell/") == item or node.removeprefix("port/") == item for item in focus)
    }
    selected.update(
        endpoint
        for edge in edges
        if edge.net in focus
        for endpoint in (edge.source, edge.target)
    )
    if not selected:
        raise SystemExit(f"Nothing matched --focus values: {', '.join(focus)}")

    forward: dict[str, set[str]] = defaultdict(set)
    backward: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        forward[edge.source].add(edge.target)
        backward[edge.target].add(edge.source)

    queue = deque((node, 0) for node in selected)
    while queue:
        node, distance = queue.popleft()
        if depth is not None and distance >= depth:
            continue
        neighbors: set[str] = set()
        if direction in {"fanout", "both"}:
            neighbors.update(forward[node])
        if direction in {"fanin", "both"}:
            neighbors.update(backward[node])
        for neighbor in neighbors - selected:
            selected.add(neighbor)
            queue.append((neighbor, distance + 1))
    return selected


def dot_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def find_register_waves(
    starts: set[str],
    cells: dict[str, Cell],
    edges: list[Edge],
    selected: set[str],
    direction: str,
) -> dict[int, list[str]]:
    """Group registers by their minimum sequential distance from a start node."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.source in selected and edge.target in selected:
            if direction == "fanout":
                adjacency[edge.source].append(edge.target)
            else:
                adjacency[edge.target].append(edge.source)

    distances = {start: 0 for start in starts}
    queue = deque(starts)
    while queue:
        node = queue.popleft()
        for target in adjacency[node]:
            weight = int(target in cells and is_sequential(cells[target]))
            distance = distances[node] + weight
            if target in distances and distances[target] <= distance:
                continue
            distances[target] = distance
            if weight:
                queue.append(target)
            else:
                queue.appendleft(target)

    waves: dict[int, list[str]] = defaultdict(list)
    for node, distance in distances.items():
        if node in cells and is_sequential(cells[node]):
            waves[distance].append(node)
    return {wave: sorted(nodes) for wave, nodes in sorted(waves.items())}


def friendly_cell_name(cell: Cell) -> str:
    """Translate a SKY130 cell name into a concise logic description."""
    name = cell.cell_type.removeprefix("sky130_fd_sc_hd__")
    name = re.sub(r"_\d+$", "", name)
    inverted_inputs = sum(pin.endswith("_N") for pin, _ in cell.connections)

    fixed_names = {
        "inv": "NOT",
        "buf": "BUFFER",
        "clkbuf": "CLOCK BUFFER",
        "clkinv": "CLOCK INVERTER",
        "mux2": "2:1 MUX",
        "mux4": "4:1 MUX",
        "fa": "FULL ADDER",
        "ha": "HALF ADDER",
        "conb": "CONSTANT",
    }
    if name in fixed_names:
        return fixed_names[name]

    if name.startswith("dfr"):
        return "D FLIP-FLOP\nactive-low reset"
    if name.startswith("dfs"):
        return "D FLIP-FLOP\nset"
    if name.startswith("df"):
        return "D FLIP-FLOP"
    if name.startswith("dl"):
        return "D LATCH"

    simple = SIMPLE_GATE_RE.fullmatch(name)
    if simple:
        operation, inputs = simple.groups()
        label = f"{operation.upper()} ({inputs}-input)"
        if inverted_inputs:
            label += f"\n{inverted_inputs} inverted input"
            if inverted_inputs != 1:
                label += "s"
        return label

    compound = COMPOUND_GATE_RE.fullmatch(name)
    if compound:
        first, groups, second, inverted_output = compound.groups()
        first_name = "AND" if first == "a" else "OR"
        second_name = "AND" if second == "a" else "OR"
        label = f"{first_name}-{second_name} ({'-'.join(groups)})"
        qualifiers: list[str] = []
        if inverted_inputs:
            qualifiers.append(
                f"{inverted_inputs} inverted input" + ("s" if inverted_inputs != 1 else "")
            )
        if inverted_output:
            qualifiers.append("inverted output")
        if qualifiers:
            label += "\n" + ", ".join(qualifiers)
        return label

    return name.replace("_", " ").upper()


def make_dot(
    source_name: str,
    cells: dict[str, Cell],
    ports: set[str],
    edges: list[Edge],
    selected: set[str],
    show_net_labels: bool,
    show_instance_names: bool,
    raw_cell_names: bool,
    register_waves: dict[int, list[str]] | None,
) -> str:
    lines = [
        "digraph netlist {",
        f"  graph [rankdir=LR, label={dot_quote(source_name)}, labelloc=t, "
        'fontname="Helvetica", overlap=false, splines=polyline];',
        '  node [fontname="Helvetica", fontsize=9, shape=box, style="rounded,filled"];',
        '  edge [fontname="Helvetica", fontsize=7, color="#667085", arrowsize=0.6];',
    ]
    for node in sorted(selected):
        if node.startswith("port/"):
            port = node.removeprefix("port/")
            lines.append(
                f"  {dot_quote(node)} [label={dot_quote(port)}, shape=oval, "
                'fillcolor="#d1fadf", color="#12b76a"];'
            )
            continue
        cell = cells[node]
        short_type = cell.cell_type.removeprefix("sky130_fd_sc_hd__")
        if is_sequential(cell):
            fill, color, penwidth = "#fef0c7", "#f79009", "1.5"
        elif "__clk" in cell.cell_type:
            fill, color, penwidth = "#e0f2fe", "#0ba5ec", "1.0"
        else:
            fill, color, penwidth = "#f2f4f7", "#98a2b3", "1.0"
        cell_label = short_type if raw_cell_names else friendly_cell_name(cell)
        label = f"{cell.instance}\n{cell_label}" if show_instance_names else cell_label
        tooltip = f"{cell.instance}: {cell.cell_type}"
        lines.append(
            f"  {dot_quote(node)} [label={dot_quote(label)}, fillcolor={dot_quote(fill)}, "
            f"color={dot_quote(color)}, penwidth={penwidth}, tooltip={dot_quote(tooltip)}];"
        )

    if register_waves:
        for wave, nodes in register_waves.items():
            members = " ".join(f"{dot_quote(node)};" for node in nodes)
            lines.append(f"  subgraph register_wave_{wave} {{ rank=same; {members} }}")

    for edge in edges:
        if edge.source not in selected or edge.target not in selected:
            continue
        attributes: list[str] = []
        if show_net_labels:
            attributes.append(f"label={dot_quote(edge.net)}")
        if edge.target_pin in {"CLK", "GATE"}:
            attributes.extend(['color="#0ba5ec"', "style=dashed"])
        elif edge.target_pin in {"RESET_B", "RESET", "SET_B", "SET"}:
            attributes.extend(['color="#f79009"', "style=dotted"])
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f"  {dot_quote(edge.source)} -> {dot_quote(edge.target)}{suffix};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_graph(dot: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".dot":
        output.write_text(dot)
        return
    output_format = output.suffix.lower().lstrip(".")
    if output_format not in {"svg", "png", "pdf"}:
        raise SystemExit("Output extension must be .dot, .svg, .png, or .pdf")
    graphviz = shutil.which("dot")
    if graphviz is None:
        raise SystemExit(
            f"Graphviz `dot` is required for {output.suffix} output. "
            "Install graphviz or use a .dot output."
        )
    subprocess.run(
        [graphviz, f"-T{output_format}", "-o", str(output)],
        input=dot,
        text=True,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", nargs="?", type=Path, default=DEFAULT_NETLIST)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--focus",
        action="append",
        default=[],
        metavar="SIGNAL_OR_INSTANCE",
        help="Limit the graph to a signal/port/instance and its neighborhood (repeatable)",
    )
    parser.add_argument("--depth", type=int, default=3, help="Traversal depth used with --focus")
    parser.add_argument(
        "--data-paths-from",
        metavar="SIGNAL",
        help=(
            "Trace all data fanout from a signal through combinational logic and registers; "
            "omits clock/reset/set edges and net labels"
        ),
    )
    parser.add_argument(
        "--data-paths-to",
        metavar="SIGNAL",
        help=(
            "Trace all data fanin feeding a signal through combinational logic and registers; "
            "omits clock/reset/set edges and net labels"
        ),
    )
    parser.add_argument(
        "--direction",
        choices=("fanin", "fanout", "both"),
        default="both",
        help="Traversal direction used with --focus",
    )
    parser.add_argument("--no-net-labels", action="store_true", help="Hide net names on edges")
    parser.add_argument(
        "--show-instance-names",
        action="store_true",
        help="Show instance IDs such as U228 in component labels",
    )
    parser.add_argument(
        "--raw-cell-names",
        action="store_true",
        help="Use SKY130 names such as or4b_2 instead of friendly logic names",
    )
    parser.add_argument("--include-power", action="store_true", help="Include power connections")
    parser.add_argument(
        "--include-physical",
        action="store_true",
        help="Include tap, decap, fill, and diode cells",
    )
    args = parser.parse_args()

    if not args.netlist.is_file():
        raise SystemExit(f"Netlist does not exist: {args.netlist}")
    if args.depth < 0:
        parser.error("--depth must be non-negative")
    if args.data_paths_from and args.data_paths_to:
        parser.error("--data-paths-from and --data-paths-to are mutually exclusive")
    if (args.data_paths_from or args.data_paths_to) and args.focus:
        parser.error("data-path tracing options cannot be combined with --focus")

    text = strip_comments(args.netlist.read_text())
    ports = parse_ports(text)
    parsed_cells, skipped = parse_cells(text, args.include_physical)
    cells, connected_ports, edges = build_graph(parsed_cells, ports, args.include_power)
    data_focus = args.data_paths_from or args.data_paths_to
    data_direction = "fanout" if args.data_paths_from else "fanin"
    if data_focus:
        edges = [edge for edge in edges if edge.target_pin not in CONTROL_PINS]
    all_nodes = set(cells) | {f"port/{port}" for port in connected_ports}
    focus = [data_focus] if data_focus else args.focus
    direction = data_direction if data_focus else args.direction
    depth = None if data_focus else args.depth
    selected = select_nodes(focus, depth, direction, all_nodes, edges)
    register_waves = None
    if data_focus:
        wave_starts = {
            node
            for node in selected
            if node.removeprefix("cell/") == data_focus
            or node.removeprefix("port/") == data_focus
        }
        wave_starts.update(
            (edge.source if data_direction == "fanout" else edge.target)
            for edge in edges
            if edge.net == data_focus
        )
        register_waves = find_register_waves(
            wave_starts, cells, edges, selected, data_direction
        )
    dot = make_dot(
        args.netlist.name,
        cells,
        connected_ports,
        edges,
        selected,
        not (args.no_net_labels or data_focus),
        args.show_instance_names,
        args.raw_cell_names,
        register_waves,
    )
    write_graph(dot, args.output)
    visible_edges = sum(edge.source in selected and edge.target in selected for edge in edges)
    print(
        f"Wrote {args.output} with {len(selected)} nodes and {visible_edges} edges "
        f"({skipped} physical-only cells skipped)."
    )


if __name__ == "__main__":
    main()

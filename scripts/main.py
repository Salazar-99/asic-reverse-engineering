"""Extract a structural, gate-level Verilog netlist from the puzzle GDS.

The file embeds the SKY130 HD standard cells and their pin labels.  Therefore
this extractor does not need a separate SKY130 PDK: it discovers each cell's
pins from GDS text labels and reconstructs connectivity from LI/metal/via
geometry.
"""

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from klayout import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GDS = PROJECT_ROOT / "puzzle.gds"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "puzzle_extracted.v"


# SKY130 GDS layer/datatype pairs used by this design.  The contact/via layer
# belongs to both adjacent routing levels so each is merged into its conductor.
ROUTING_LEVELS = (
    ((67, 20), (67, 44)),  # li1 + licon1
    ((68, 20), (67, 44), (68, 44)),  # met1 + licon1 + mcon
    ((69, 20), (68, 44), (69, 44)),  # met2 + via1 + via2
    ((70, 20), (69, 44), (70, 44)),  # met3 + via2 + via3
    ((71, 20), (70, 44), (71, 44)),  # met4 + via3 + via4
    ((72, 20), (71, 44)),  # met5 + via4
)
VIA_LAYERS = ((67, 44), (68, 44), (69, 44), (70, 44), (71, 44))
LABEL_LAYERS = (
    (64, 5, 1),  # nwell label (VPB), physically connected through met1
    (64, 59, 1),  # pwell label (VNB), physically connected through met1
    (67, 5, 0),  # li1
    (68, 5, 1),  # met1
    (69, 5, 2),  # met2
    (70, 5, 3),  # met3
    (71, 5, 4),  # met4
    (72, 5, 5),  # met5
)


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


@dataclass(frozen=True)
class Pin:
    instance: str
    cell: str
    name: str
    component: int


class ComponentIndex:
    """Merged polygons on one routing level, indexed by a covered point."""

    def __init__(self, region: db.Region, first_id: int) -> None:
        self.region = region.merged()
        self.ids = {}
        for offset, polygon in enumerate(self.region.each()):
            self.ids[polygon.to_s()] = first_id + offset
        self.size = len(self.ids)

    def at(self, point: db.Point) -> int | None:
        # Labels are normally placed within the pin, but a one-DBU box also
        # handles labels located exactly on a polygon boundary.
        query = db.Region(db.Box(point - db.Vector(1, 1), point + db.Vector(1, 1)))
        # KLayout's Region selection methods modify their receiver in place.
        # Query a duplicate so subsequent terminal lookups see all conductors.
        matches = self.region.dup().select_covering(query)
        for polygon in matches.each():
            return self.ids[polygon.to_s()]
        return None


def flattened_region(layout: db.Layout, top: db.Cell, layer_pairs: tuple[tuple[int, int], ...]) -> db.Region:
    region = db.Region()
    for layer, datatype in layer_pairs:
        index = layout.find_layer(layer, datatype)
        if index is not None:
            region.insert(top.begin_shapes_rec(index))
    return region


def cell_pin_labels(layout: db.Layout, cell: db.Cell, transform: db.Trans) -> list[tuple[str, int, db.Point]]:
    """Return unique (pin name, level, location) labels for one placed cell."""
    labels = []
    seen = set()
    for layer_number, datatype, level in LABEL_LAYERS:
        index = layout.find_layer(layer_number, datatype)
        if index is None:
            continue
        for shape in cell.shapes(index).each():
            if not shape.is_text():
                continue
            text = shape.text
            location = (transform * text.trans).disp
            key = (text.string, level, location.x, location.y)
            if key not in seen:
                seen.add(key)
                labels.append((text.string, level, location))
    return labels


def extract(gds_path: Path, output_path: Path) -> None:
    layout = db.Layout()
    layout.read(str(gds_path))
    top = layout.top_cell()
    if top is None:
        raise ValueError("GDS does not contain a top-level cell")

    indices: list[ComponentIndex] = []
    next_id = 0
    for level in ROUTING_LEVELS:
        index = ComponentIndex(flattened_region(layout, top, level), next_id)
        indices.append(index)
        next_id += index.size
    nets = DisjointSet(next_id)

    # Every via overlaps one component on each adjacent routing level.
    for level, via_layer in enumerate(VIA_LAYERS):
        vias = flattened_region(layout, top, (via_layer,)).merged()
        for via in vias.each():
            center = via.bbox().center()
            lower = indices[level].at(center)
            upper = indices[level + 1].at(center)
            if lower is not None and upper is not None:
                nets.union(lower, upper)

    pins: list[Pin] = []
    for number, instance in enumerate(top.each_inst()):
        cell = instance.cell
        if not cell.name.startswith("sky130_fd_sc_hd__"):
            continue
        for name, level, location in cell_pin_labels(layout, cell, instance.trans):
            component = indices[level].at(location)
            if component is not None:
                pins.append(Pin(f"U{number}", cell.name, name, component))

    ports: dict[int, list[str]] = defaultdict(list)
    for name, level, location in cell_pin_labels(layout, top, db.Trans()):
        component = indices[level].at(location)
        if component is not None:
            ports[nets.find(component)].append(name)

    # Prefer an explicit top-level GDS label as the Verilog net name.
    root_names: dict[int, str] = {}
    used_names = set()
    for root, names in ports.items():
        preferred = min(set(names), key=lambda value: (value.startswith("O["), value))
        root_names[root] = preferred
        used_names.add(preferred)

    unnamed = 0
    for pin in pins:
        root = nets.find(pin.component)
        if root not in root_names:
            while f"net_{unnamed}" in used_names:
                unnamed += 1
            root_names[root] = f"net_{unnamed}"
            used_names.add(root_names[root])
            unnamed += 1

    pins_by_instance: dict[tuple[str, str], list[Pin]] = defaultdict(list)
    for pin in pins:
        pins_by_instance[(pin.instance, pin.cell)].append(pin)

    port_names = sorted({name for names in ports.values() for name in names})
    port_groups: dict[str, list[int]] = defaultdict(list)
    scalar_ports = set()
    for name in port_names:
        match = re.fullmatch(r"(.+)\[(\d+)\]", name)
        if match:
            port_groups[match.group(1)].append(int(match.group(2)))
        else:
            scalar_ports.add(name)
    module_ports = sorted(scalar_ports | set(port_groups))
    port_declarations = [f"  inout {name};" for name in sorted(scalar_ports)]
    port_declarations.extend(
        f"  inout [{max(bits)}:{min(bits)}] {name};"
        for name, bits in sorted(port_groups.items())
    )
    wire_names = sorted(
        name for root, name in root_names.items() if root not in ports and name not in {"VPWR", "VGND", "VPB", "VNB"}
    )
    lines = [
        "// Generated by main.py from geometry and pin labels in puzzle.gds.",
        "// Ports are declared inout because GDS does not encode port direction.",
        f"module {top.name} ({', '.join(module_ports)});",
        *port_declarations,
        *(f"  wire {name};" for name in wire_names),
        "",
    ]
    for (instance, cell), instance_pins in sorted(pins_by_instance.items()):
        pin_nets: dict[str, str] = {}
        for pin in instance_pins:
            net = root_names[nets.find(pin.component)]
            previous = pin_nets.setdefault(pin.name, net)
            if previous != net:
                raise ValueError(
                    f"{instance}.{pin.name} is labeled on multiple disconnected nets: "
                    f"{previous}, {net}"
                )
        connections = ", ".join(
            f".{name}({net})" for name, net in sorted(pin_nets.items())
        )
        lines.append(f"  {cell} {instance} ({connections});")
    lines.extend(["endmodule", ""])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    print(
        f"Extracted {len(pins_by_instance)} standard-cell instances, "
        f"{len(root_names)} named nets, and {len(port_names)} top-level ports to {output_path}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract a SKY130 GDS gate-level netlist with KLayout.")
    parser.add_argument("gds", nargs="?", type=Path, default=DEFAULT_GDS)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    extract(args.gds, args.output)

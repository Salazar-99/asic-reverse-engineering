.PHONY: all netlist compile test clean

UV := uv run python
OUTPUTS := outputs
NETLIST := $(OUTPUTS)/puzzle_extracted.v
PUZZLE_VVP := $(OUTPUTS)/puzzle.vvp
TB_VVP := $(OUTPUTS)/tb_puzzle.vvp

all: test

netlist: $(NETLIST)

compile: $(PUZZLE_VVP)

test: $(TB_VVP)
	vvp $(TB_VVP)

$(NETLIST): puzzle.gds scripts/main.py
	@mkdir -p $(OUTPUTS)
	$(UV) scripts/main.py

$(PUZZLE_VVP): $(NETLIST) scripts/compile_netlist.py
	$(UV) scripts/compile_netlist.py

$(TB_VVP): $(NETLIST) scripts/compile_netlist.py scripts/tb_puzzle.v
	$(UV) scripts/compile_netlist.py --testbench

clean:
	rm -rf $(OUTPUTS)

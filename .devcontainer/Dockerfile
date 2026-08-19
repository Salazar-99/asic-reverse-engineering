ARG VARIANT="3.13-bookworm"
FROM mcr.microsoft.com/devcontainers/python:1-${VARIANT}

USER root

ARG OPEN_PDKS_REF="master"

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        autoconf \
        automake \
        bison \
        build-essential \
        ca-certificates \
        flex \
        gawk \
        git \
        iverilog \
        klayout \
        libglu1-mesa-dev \
        libtool \
        libx11-dev \
        libxext-dev \
        libxmu-dev \
        libxrender-dev \
        magic \
        netgen \
        pkg-config \
        tcl-dev \
        tk-dev \
    && rm -rf /var/lib/apt/lists/*

# Build a self-contained SKY130A installation.  Building only sky130A avoids
# downloading or compiling the unrelated GF180 PDK and sky130B/ReRAM variant.
RUN git clone --depth 1 --branch "${OPEN_PDKS_REF}" \
        https://github.com/fossi-foundation/open_pdks.git /opt/open_pdks \
    && cd /opt/open_pdks \
    && ./configure \
        --prefix=/opt/pdk \
        --enable-sky130-pdk \
        --disable-gf180mcu-pdk \
        --with-sky130-variants=A \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /opt/open_pdks

# Functional Verilog models for gate-level elaboration (make compile / make test).
RUN git clone --depth 1 \
        https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd.git \
        /opt/skywater-pdk-libs-sky130_fd_sc_hd

RUN python -m pip install --no-cache-dir uv

ENV PDK_ROOT=/opt/pdk/share/pdk \
    PDK=sky130A \
    SKY130_HD_ROOT=/opt/skywater-pdk-libs-sky130_fd_sc_hd \
    UV_PROJECT_ENVIRONMENT=/home/vscode/.venvs/asic-reverse-engineering

USER vscode

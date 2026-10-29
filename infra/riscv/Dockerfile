# Source-only recipe until the coordinator grants the shared build slot.
FROM verilator/verilator:v5.042@sha256:63e38d3b18ca8799d2821c2c254b36c449eb80ea7f38769e919849996f0b7cd7
ENV DEBIAN_FRONTEND=noninteractive
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git make g++ device-tree-compiler libboost-regex-dev libboost-system-dev \
    gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf python3 python3-venv libpython3.12t64 \
    && rm -rf /var/lib/apt/lists/*
ARG SPIKE_BUILD_JOBS=4
RUN case "$SPIKE_BUILD_JOBS" in 2|4) ;; *) exit 2;; esac && \
    git init /opt/spike-src && \
    git -C /opt/spike-src remote add origin https://github.com/riscv-software-src/riscv-isa-sim.git && \
    git -C /opt/spike-src fetch --depth 1 origin 4c6a9bc79dab80616932949707e7c0471d8e9e54 && \
    git -C /opt/spike-src checkout --detach FETCH_HEAD && \
    test "$(git -C /opt/spike-src rev-parse HEAD)" = 4c6a9bc79dab80616932949707e7c0471d8e9e54 && \
    git -C /opt/spike-src submodule update --init --recursive --depth 1 && \
    mkdir /opt/spike-src/build && cd /opt/spike-src/build && \
    ../configure --prefix=/opt/spike && make -j"$SPIKE_BUILD_JOBS" && make install && \
    git -C /opt/spike-src rev-parse HEAD > /opt/spike/UPSTREAM_REVISION && \
    git -C /opt/spike-src submodule status --recursive > /opt/spike/UPSTREAM_SUBMODULES && \
    rm -rf /opt/spike-src
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
    cocotb==2.0.1 pydantic==2.12.3 pytest==8.4.2
ENV PATH="/opt/venv/bin:/opt/spike/bin:${PATH}" PYTHONPATH=/workspace/src PYTHONDONTWRITEBYTECODE=1
RUN mkdir /opt/veriforge && dpkg-query -W > /opt/veriforge/PACKAGE_VERSIONS
WORKDIR /workspace
USER 1000:1000
ENTRYPOINT ["python", "-m", "veriforge.riscv"]

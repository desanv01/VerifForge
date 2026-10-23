FROM verilator/verilator:v5.042@sha256:63e38d3b18ca8799d2821c2c254b36c449eb80ea7f38769e919849996f0b7cd7
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir cocotb==2.0.1 pydantic==2.12.3 pytest==8.4.2
RUN apt-get update && apt-get install -y --no-install-recommends libpython3.12t64 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/venv/bin:${PATH}" PYTHONPATH=/workspace/src
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "veriforge.rtl"]

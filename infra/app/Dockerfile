ARG RTL_IMAGE=veriforge-rtl:dev
FROM ${RTL_IMAGE}
# Keep the simulator image, but replace its spike dependencies with uv.lock.
RUN /opt/venv/bin/pip install --no-cache-dir uv==0.11.25
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/opt/app-venv UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-install-project --python /usr/bin/python3.12
COPY src ./src
COPY targets ./targets
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH="/opt/app-venv/bin:/opt/venv/bin:${PATH}" PYTHONPATH=/workspace/src PYTHONUNBUFFERED=1
RUN uv sync --locked --python /usr/bin/python3.12
COPY tests/integration ./tests/integration
ENTRYPOINT []
CMD ["uvicorn", "veriforge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

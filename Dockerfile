FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Java is required by the optional H2/Dameng JDBC bridge; unixODBC supports
# SQL Server deployments. Runtime-generated Python still executes only in Docker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless unixodbc libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install one immutable platform-specific CUDA wheel from PyTorch's official
# download host. The index page is reset by some network paths, while this
# canonical wheel endpoint remains resumable and avoids CUDA-version drift.
ARG DATAAGENT_TORCH_WHEEL_URL=https://download-r2.pytorch.org/whl/cu124/torch-2.6.0%2Bcu124-cp311-cp311-linux_x86_64.whl
ARG DATAAGENT_PYPI_INDEX_URL=https://pypi.org/simple

RUN pip install --no-cache-dir \
      --index-url "${DATAAGENT_PYPI_INDEX_URL}" \
      "${DATAAGENT_TORCH_WHEEL_URL}"

COPY pyproject.toml README.md LICENSE ./
# Resolve and cache the large optional runtime dependency set independently of
# application source changes. A minimal package skeleton lets hatchling read
# the project metadata; the real package is installed without dependencies in
# the next layer.
RUN mkdir -p src/dataagent \
    && touch src/dataagent/__init__.py \
    && pip install --no-cache-dir \
      --index-url "${DATAAGENT_PYPI_INDEX_URL}" \
      ".[full]" \
    && rm -rf src

COPY src ./src
RUN pip install --no-cache-dir \
      --index-url "${DATAAGENT_PYPI_INDEX_URL}" \
      --no-deps \
      "."

RUN mkdir -p /app/data /app/uploads

EXPOSE 8065 8066

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8065/echo/ok', timeout=3)"

CMD ["dataagent"]

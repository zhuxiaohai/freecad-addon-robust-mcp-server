# FreeCAD Robust MCP Server Dockerfile
# Multi-stage build with BuildKit optimizations for multi-arch support
#
# Uses Alpine Linux for minimal image size and reduced CVE surface.
# Alpine has significantly fewer vulnerabilities than Debian-based images.
#
# Build:
#   docker build -t freecad-mcp .
#
# Build multi-arch:
#   docker buildx build --platform linux/amd64,linux/arm64 -t freecad-mcp .
#
# Run (stdio mode, for Cursor/Claude Code):
#   docker run --rm -i freecad-mcp
#
# Run (HTTP mode, for cloud/compose deployment):
#   docker run -d -p 8000:8000 -e FREECAD_TRANSPORT=http freecad-mcp
#
# Corporate registry (base images mirrored on hub.designorder.cn):
#   docker build --build-arg DOCKER_REGISTRY=hub.designorder.cn/ -t freecad-mcp .

# Prefix for base images (empty = Docker Hub). Corporate CI uses hub.designorder.cn/.
ARG DOCKER_REGISTRY=hub.designorder.cn/
# Alpine apk / PyPI mirrors for corporate networks that block dl-cdn.alpinelinux.org and pypi.org.
# Override at build time: --build-arg APK_MIRROR= --build-arg PIP_INDEX_URL= (empty = upstream defaults)
ARG APK_MIRROR=https://mirrors.aliyun.com/alpine/
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# =============================================================================
# Stage 1: Builder - Install dependencies and build the package
# =============================================================================
FROM ${DOCKER_REGISTRY}python:3.11-alpine AS builder

ARG APK_MIRROR
ARG PIP_INDEX_URL

# Replace Alpine CDN and install build deps (Jenkins often cannot reach dl-cdn.alpinelinux.org).
# hadolint ignore=DL3018
RUN if [ -n "${APK_MIRROR}" ]; then \
      sed -i "s|https\?://dl-cdn.alpinelinux.org/alpine/|${APK_MIRROR}|g" /etc/apk/repositories; \
    fi && \
    apk add --no-cache \
    build-base \
    libffi-dev

# Set up working directory
WORKDIR /app

# Upgrade pip to fix CVE-2025-8869, then install uv for fast dependency management
# hadolint ignore=DL3013
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "${PIP_INDEX_URL}" ]; then export PIP_INDEX_URL="${PIP_INDEX_URL}"; fi && \
    pip install --no-cache-dir --upgrade "pip>=25.3" && \
    pip install --no-cache-dir --no-compile uv

# Copy only dependency files first for better layer caching
# Include uv.lock for reproducible builds with locked dependency versions
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Version for setuptools-scm when building without git (e.g., in Docker)
# This can be overridden at build time with --build-arg VERSION=x.y.z
ARG VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

# Create virtual environment and install dependencies using locked versions
# Using uv cache mount for faster rebuilds
# --frozen ensures uv.lock is used exactly without updates
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${PIP_INDEX_URL}" ]; then export UV_INDEX_URL="${PIP_INDEX_URL}"; fi && \
    uv venv /opt/venv && \
    UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-editable

# =============================================================================
# Stage 2: Runtime - Minimal image for running the server
# =============================================================================
ARG DOCKER_REGISTRY=hub.designorder.cn/
ARG APK_MIRROR=https://mirrors.aliyun.com/alpine/
FROM ${DOCKER_REGISTRY}python:3.11-alpine AS runtime

ARG APK_MIRROR

# Replace Alpine CDN and upgrade packages (same corporate network issue as builder stage).
# hadolint ignore=DL3018
RUN if [ -n "${APK_MIRROR}" ]; then \
      sed -i "s|https\?://dl-cdn.alpinelinux.org/alpine/|${APK_MIRROR}|g" /etc/apk/repositories; \
    fi && \
    apk upgrade --no-cache

# Labels for container metadata (OCI Image Spec)
# Note: version, revision, and created are set dynamically in CI/CD workflows
LABEL org.opencontainers.image.title="FreeCAD Robust MCP Server" \
      org.opencontainers.image.description="Robust MCP Server for FreeCAD integration with AI assistants" \
      org.opencontainers.image.url="https://github.com/spkane/freecad-robust-mcp-and-more" \
      org.opencontainers.image.source="https://github.com/spkane/freecad-robust-mcp-and-more" \
      org.opencontainers.image.documentation="https://github.com/spkane/freecad-robust-mcp-and-more#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="Sean P. Kane" \
      org.opencontainers.image.authors="Sean P. Kane <spkane@gmail.com>" \
      org.opencontainers.image.base.name="hub.designorder.cn/python:3.11-alpine"

# Create non-root user for security (Alpine uses addgroup/adduser)
RUN addgroup -g 1000 mcpuser && \
    adduser -u 1000 -G mcpuser -s /bin/sh -D mcpuser

# Remove pip, setuptools, and wheel from system Python to fix CVEs
# - The base image has pip with CVE-2025-8869
# - setuptools vendors jaraco.context 5.3.0 with GHSA-58pv-8j8x-9vj2
# Since we use a pre-built venv, we don't need these in system Python at runtime.
# This eliminates the vulnerabilities without affecting functionality.
# hadolint ignore=DL3013
RUN pip uninstall -y pip setuptools wheel 2>/dev/null || true && \
    rm -rf /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/site-packages/pkg_resources*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Default to xmlrpc mode (requires FreeCAD running externally)
    FREECAD_MODE="xmlrpc" \
    FREECAD_SOCKET_HOST="host.docker.internal" \
    FREECAD_SOCKET_PORT="9876" \
    FREECAD_XMLRPC_PORT="9875" \
    FREECAD_TIMEOUT_MS="30000"

# Declare HTTP port for MCP HTTP transport mode
# Enable with: docker run -e FREECAD_TRANSPORT=http -p 8000:8000 freecad-mcp
EXPOSE 8000

# Switch to non-root user
USER mcpuser
WORKDIR /home/mcpuser

# Health check - verify the server is running:
# - HTTP mode (FREECAD_TRANSPORT=http): verifies port is accepting TCP connections
# - stdio mode (default): verifies Python package imports successfully
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,socket; t=os.environ.get('FREECAD_TRANSPORT','stdio'); p=int(os.environ.get('FREECAD_HTTP_PORT','8000')); socket.create_connection(('localhost',p),timeout=5).close() if t=='http' else (__import__('freecad_mcp'),print('ok'))" || exit 1

# Default command - run the MCP server in stdio mode
# For HTTP mode: docker run -e FREECAD_TRANSPORT=http -p 8000:8000 freecad-mcp
ENTRYPOINT ["freecad-mcp"]

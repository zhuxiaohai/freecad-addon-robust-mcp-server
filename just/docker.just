# Docker build and run commands.
#
# This repo builds ONLY the MCP server image (hub.designorder.cn/freecad-robust-mcp).
# The bridge image (hub.designorder.cn/freecad-bridge) is built in service_freecad_bridge.
#
# Compose stack (FreeCAD + Bridge + MCP) — image tags in deploy/.env:
#   just docker::compose-pull-bridge | compose-build-mcp | compose-up | publish-dev
#
# Route 3 only (MCP container → FreeCAD on host) — separate from compose:
#   just docker::build | run-http | test
#
# See deploy/README.md for the full workflow.

# Image repo names for Route 3 and upstream release (not compose stack tags).
image_name := "freecad-robust-mcp"
bridge_image_name := "freecad-bridge"
registry := "spkane"
gui_test_image := "freecad-gui-test"

# Compose stack: ALL image tags and deploy settings live in deploy/.env only.
#   FREECAD_BRIDGE_IMAGE, FREECAD_MCP_IMAGE, FREECAD_TAG, FREECAD_MCP_PORT
deploy_compose := project_root + "/deploy/docker-compose.yml"
deploy_env := project_root + "/deploy/.env"

# Project root directory (justfile_directory() returns the main justfile's directory)
project_root := justfile_directory()

# Build Docker image for local architecture only (Route 3: MCP container → host FreeCAD).
# Not used by deploy compose; compose uses docker::compose-build-* instead.
build:
    docker build -t {{image_name}} {{project_root}}

# Build Docker image with specific tag
build-tag tag:
    docker build --load -t {{image_name}}:{{tag}} {{project_root}}

# Validate multi-architecture build (amd64 and arm64)
# This is a dry-run that verifies both architectures compile successfully.
# The build populates the builder cache but does NOT produce a usable image.
# Use cases:
#   - CI validation before pushing (verify PR doesn't break either arch)
#   - Local verification that changes build on both architectures
# To actually publish a multi-arch image, use: just docker::build-push
build-multi: setup-buildx
    docker buildx build --platform linux/amd64,linux/arm64 -t {{image_name}} {{project_root}}

# Build and push multi-architecture image to registry (produces usable multi-arch image)
build-push tag="latest": setup-buildx
    docker buildx build --platform linux/amd64,linux/arm64 \
        -t {{registry}}/{{image_name}}:{{tag}} \
        --push {{project_root}}

# Show image tags from deploy/.env (single source of truth)
publish-show:
    #!/usr/bin/env bash
    set -euo pipefail
    ENV_FILE="{{deploy_env}}"
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: Missing ${ENV_FILE}"
        echo "  cp deploy/.env.example deploy/.env"
        exit 1
    fi
    echo "Compose stack settings (deploy/.env — single source of truth):"
    grep -E '^(FREECAD_BRIDGE_IMAGE|FREECAD_MCP_IMAGE|DOCKER_REGISTRY|FREECAD_TAG|FREECAD_MCP_PORT)=' "$ENV_FILE" || true
    echo ""
    echo "Resolved by docker compose config:"
    docker compose -f {{deploy_compose}} config --images
    echo ""
    echo "Dev workflow (MCP built here; bridge from service_freecad_bridge or registry):"
    echo "  just docker::compose-pull-bridge     # pull hub.designorder.cn/freecad-bridge:dev"
    echo "  just docker::compose-build-mcp       # build MCP locally"
    echo "  just docker::compose-up               # run stack"
    echo "  just docker::publish-dev              # build + push MCP only"
    echo "  just docker::publish 1.0.0            # semver MCP release"
    echo "  just docker::clean-legacy-tags        # remove Route 3 freecad-*:latest tags"

# Build compose MCP image (output tag = deploy/.env FREECAD_MCP_IMAGE)
compose-build:
    docker compose -f {{deploy_compose}} build freecad-mcp

# Pull bridge image (built in service_freecad_bridge repo / Jenkins)
compose-pull-bridge:
    #!/usr/bin/env bash
    set -euo pipefail
    ENV_FILE="{{deploy_env}}"
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: Missing ${ENV_FILE}"
        echo "  cp deploy/.env.example deploy/.env"
        exit 1
    fi
    IMAGE=$(grep '^FREECAD_BRIDGE_IMAGE=' "$ENV_FILE" | cut -d= -f2-)
    echo "Pulling bridge image (built in service_freecad_bridge repo): ${IMAGE}"
    docker pull "${IMAGE}"

# Alias for older docs/scripts — bridge is not built in this repo
compose-build-bridge:
    @just docker::compose-pull-bridge

compose-build-mcp:
    docker compose -f {{deploy_compose}} build freecad-mcp

# Start compose stack without rebuilding (uses local images from deploy/.env tags)
compose-up:
    docker compose -f {{deploy_compose}} up -d --no-build

# Remove legacy local-only tags from the old compose workflow (freecad-*:latest)
clean-legacy-tags:
    #!/usr/bin/env bash
    set -euo pipefail
    for img in "freecad-bridge:latest" "freecad-robust-mcp:latest"; do
        if docker image inspect "$img" > /dev/null 2>&1; then
            echo "Removing legacy tag: $img"
            docker rmi "$img" || true
        fi
    done
    echo "Done. Keep only deploy/.env tags (e.g. hub.designorder.cn/freecad-bridge:dev)."

# Push MCP image only (must already be built via compose-build-mcp)
publish-push-mcp:
    #!/usr/bin/env bash
    set -euo pipefail
    COMPOSE_FILE="{{deploy_compose}}"
    IMAGE=$(docker compose -f "$COMPOSE_FILE" config --format json \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['services']['freecad-mcp']['image'])")
    echo "Pushing ${IMAGE} ..."
    docker push "${IMAGE}"
    echo "✓ Pushed ${IMAGE}"

# Push bridge image — not supported in this repo (use service_freecad_bridge)
publish-push-bridge:
    #!/usr/bin/env bash
    echo "ERROR: freecad-bridge image is built in service_freecad_bridge repo."
    echo "  Push from that repo, or pull with: just docker::compose-pull-bridge"
    exit 1

# Compose build + push MCP (same tag locally and on registry)
publish-dev-mcp:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Building freecad-mcp via compose (tag from deploy/.env) ..."
    docker compose -f {{deploy_compose}} build freecad-mcp
    just docker::publish-push-mcp

# Bridge publish — not supported in this repo (use service_freecad_bridge)
publish-dev-bridge:
    #!/usr/bin/env bash
    echo "ERROR: freecad-bridge image is built in service_freecad_bridge repo."
    echo "  Use that repo's Jenkins job or: just docker::compose-pull-bridge"
    exit 1

# Compose build + push MCP image only
publish-dev:
    @just docker::publish-dev-mcp

# Push immutable semver tag for MCP (same repo as deploy/.env, tag replaced for this build only)
publish version:
    #!/usr/bin/env bash
    set -euo pipefail
    ENV_FILE="{{deploy_env}}"
    VERSION="{{version}}"
    MCP_BASE=$(grep '^FREECAD_MCP_IMAGE=' "$ENV_FILE" | cut -d= -f2 | sed 's/:[^:]*$//')
    export FREECAD_MCP_IMAGE="${MCP_BASE}:${VERSION}"
    echo "Release build: ${FREECAD_MCP_IMAGE}"
    docker compose -f {{deploy_compose}} build freecad-mcp
    docker push "${FREECAD_MCP_IMAGE}"
    echo "✓ Pushed release image (${VERSION})"

# Build and load image for current architecture using buildx
build-load: setup-buildx
    #!/usr/bin/env bash
    set -euo pipefail
    # Detect current architecture
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  PLATFORM="linux/amd64" ;;
        aarch64|arm64) PLATFORM="linux/arm64" ;;
        *)       echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    echo "Building for detected architecture: $PLATFORM"
    docker buildx build --platform "$PLATFORM" \
        -t {{image_name}} \
        --load {{project_root}}

# Run the Docker container (connects to FreeCAD on host)
run:
    docker run --rm -i \
        --add-host=host.docker.internal:host-gateway \
        -e FREECAD_MODE=xmlrpc \
        -e FREECAD_SOCKET_HOST=host.docker.internal \
        {{image_name}}

# Run with custom environment variables
run-env *args:
    docker run --rm -i \
        --add-host=host.docker.internal:host-gateway \
        {{args}} \
        {{image_name}}

# Run container in HTTP mode (persistent, connects to FreeCAD on host)
# Use this to preview cloud deployment locally with Windows FreeCAD still on host.
# The container stays running in the background; connect via http://localhost:{port}/mcp
run-http port="8000":
    docker run -d --name freecad-mcp-http \
        --add-host=host.docker.internal:host-gateway \
        -p {{port}}:8000 \
        -e FREECAD_MODE=xmlrpc \
        -e FREECAD_SOCKET_HOST=host.docker.internal \
        -e FREECAD_TRANSPORT=http \
        -e FREECAD_HTTP_PORT=8000 \
        {{image_name}}

# Run container interactively with shell
shell:
    docker run --rm -it \
        --add-host=host.docker.internal:host-gateway \
        -e FREECAD_MODE=xmlrpc \
        -e FREECAD_SOCKET_HOST=host.docker.internal \
        --entrypoint /bin/sh \
        {{image_name}}

# Show image size and layers
inspect:
    docker images {{image_name}}
    @echo ""
    docker history {{image_name}}

# Remove local Docker image
clean:
    docker rmi {{image_name}} 2>/dev/null || true
    docker rmi {{registry}}/{{image_name}} 2>/dev/null || true

# Remove Docker images and build cache
clean-all:
    docker rmi {{image_name}} 2>/dev/null || true
    docker rmi {{registry}}/{{image_name}} 2>/dev/null || true
    docker builder prune -f
    @echo "Docker images and build cache cleaned."

# Scan Docker image for vulnerabilities (warn on all severities)
# Uses .trivyignore file to skip CVEs without available fixes
scan:
    #!/usr/bin/env bash
    set -euo pipefail
    IGNOREFILE="{{project_root}}/.trivyignore"
    IGNORE_FLAG=""
    if [ -f "$IGNOREFILE" ]; then
        IGNORE_FLAG="--ignorefile $IGNOREFILE"
        echo "Using ignore file: $IGNOREFILE"
    fi
    echo "Scanning {{image_name}} for vulnerabilities..."
    echo ""
    trivy image --severity HIGH,CRITICAL $IGNORE_FLAG {{image_name}}
    echo ""
    echo "Scanning for MEDIUM/LOW (informational)..."
    trivy image --severity MEDIUM,LOW $IGNORE_FLAG {{image_name}} || true

# Scan Docker image with strict settings (fail on HIGH or CRITICAL)
# Uses .trivyignore file to skip CVEs without available fixes
scan-strict:
    #!/usr/bin/env bash
    set -euo pipefail
    IGNOREFILE="{{project_root}}/.trivyignore"
    IGNORE_FLAG=""
    if [ -f "$IGNOREFILE" ]; then
        IGNORE_FLAG="--ignorefile $IGNOREFILE"
        echo "Using ignore file: $IGNOREFILE"
    fi
    echo "Scanning {{image_name}} for HIGH/CRITICAL vulnerabilities..."
    echo "(Build will fail if any HIGH or CRITICAL CVEs are found)"
    echo ""
    trivy image --severity HIGH,CRITICAL --exit-code 1 $IGNORE_FLAG {{image_name}}
    echo ""
    echo "✓ No HIGH or CRITICAL vulnerabilities found!"
    echo ""
    echo "Scanning for MEDIUM/LOW (informational only)..."
    trivy image --severity MEDIUM,LOW --exit-code 0 $IGNORE_FLAG {{image_name}} || true

# Scan Docker image WITHOUT ignore file (shows all CVEs including unfixable)
scan-all:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Scanning {{image_name}} for ALL vulnerabilities (ignoring .trivyignore)..."
    echo ""
    trivy image --severity HIGH,CRITICAL {{image_name}}
    echo ""
    echo "Scanning for MEDIUM/LOW..."
    trivy image --severity MEDIUM,LOW {{image_name}} || true

# Scan Docker image and output SARIF report
scan-sarif output="trivy-results.sarif":
    #!/usr/bin/env bash
    set -euo pipefail
    IGNOREFILE="{{project_root}}/.trivyignore"
    IGNORE_FLAG=""
    if [ -f "$IGNOREFILE" ]; then
        IGNORE_FLAG="--ignorefile $IGNOREFILE"
    fi
    trivy image --format sarif --output {{project_root}}/{{output}} $IGNORE_FLAG {{image_name}}
    echo "SARIF report written to {{project_root}}/{{output}}"

# Create and configure buildx builder for multi-arch builds
setup-buildx:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker buildx inspect freecad-builder > /dev/null 2>&1; then
        echo "Creating buildx builder 'freecad-builder'..."
        docker buildx create --name freecad-builder --use --bootstrap
    else
        echo "Builder 'freecad-builder' already exists, using it..."
        docker buildx use freecad-builder
    fi
    docker buildx inspect --bootstrap

# Test Docker container integration with FreeCAD
# This builds the image, starts FreeCAD with the bridge, runs the container,
# and verifies communication is working correctly.
test:
    #!/usr/bin/env bash
    set -euo pipefail

    # Source shared bridge helper functions (for graceful_kill_bridge_ports)
    . "{{project_root}}/scripts/bridge-helpers.sh"

    # Initialize variables for cleanup
    STARTED_FREECAD=false
    FREECAD_PID=""
    FREECAD_LOG=""
    MCP_INPUT=""

    # Comprehensive cleanup trap for all exit paths
    cleanup() {
        [ -n "${MCP_INPUT:-}" ] && rm -f "$MCP_INPUT" 2>/dev/null || true
        [ -n "${FREECAD_LOG:-}" ] && rm -f "$FREECAD_LOG" 2>/dev/null || true
        if [ "$STARTED_FREECAD" = true ]; then
            # Kill the just process (may not forward signals to freecadcmd child)
            [ -n "${FREECAD_PID:-}" ] && kill "$FREECAD_PID" 2>/dev/null || true
            # Kill by port to catch the orphaned freecadcmd process
            graceful_kill_bridge_ports
        fi
    }
    trap cleanup EXIT INT TERM

    echo "=========================================="
    echo "Docker Integration Test"
    echo "=========================================="
    echo ""

    # Function to check if XML-RPC server is ready using a simple POST request
    check_xmlrpc() {
        # Send a minimal XML-RPC system.listMethods call
        curl -s --max-time 2 -X POST \
            -H "Content-Type: text/xml" \
            -d '<?xml version="1.0"?><methodCall><methodName>system.listMethods</methodName></methodCall>' \
            http://localhost:9875 > /dev/null 2>&1
    }

    # Detect timeout command (not available on macOS by default)
    TIMEOUT_CMD=""
    if command -v timeout >/dev/null 2>&1; then
        TIMEOUT_CMD="timeout"
    elif command -v gtimeout >/dev/null 2>&1; then
        TIMEOUT_CMD="gtimeout"  # macOS coreutils
    fi

    # Build the Docker image
    echo "Step 1: Building Docker image..."
    docker build -t {{image_name}}:test {{project_root}}
    echo "✓ Docker image built successfully"
    echo ""

    # Check if FreeCAD bridge is already running
    echo "Step 2: Checking for FreeCAD Robust MCP Bridge..."
    if check_xmlrpc; then
        echo "✓ FreeCAD Robust MCP Bridge is already running on port 9875"
        STARTED_FREECAD=false
    else
        echo "FreeCAD Robust MCP Bridge not detected. Starting FreeCAD headless..."
        echo "  (This may take 30-60 seconds for FreeCAD to initialize...)"

        # Start FreeCAD headless in background, capturing output
        FREECAD_LOG=$(mktemp)
        just freecad::run-headless > "$FREECAD_LOG" 2>&1 &
        FREECAD_PID=$!

        # Wait for bridge to be ready (longer timeout for FreeCAD startup)
        MAX_RETRIES=60
        RETRY_COUNT=0
        while ! check_xmlrpc; do
            RETRY_COUNT=$((RETRY_COUNT + 1))
            if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
                echo "✗ ERROR: FreeCAD Robust MCP Bridge did not start within ${MAX_RETRIES}s"
                echo ""
                echo "FreeCAD log output:"
                tail -30 "$FREECAD_LOG"
                exit 1
            fi
            # Show progress less frequently to reduce noise
            if [ $((RETRY_COUNT % 5)) -eq 0 ]; then
                echo "  Waiting for bridge... ($RETRY_COUNT/$MAX_RETRIES)"
            fi
            sleep 1
        done
        rm -f "$FREECAD_LOG"
        FREECAD_LOG=""  # Clear so cleanup doesn't try to delete again
        echo "✓ FreeCAD Robust MCP Bridge started (took ${RETRY_COUNT}s)"
        STARTED_FREECAD=true
    fi
    echo ""

    # Run the container and test communication
    echo "Step 3: Running container and testing MCP communication..."
    echo "  Running Robust MCP Server in container..."

    # Send MCP initialize request via JSON-RPC over stdio
    # Using temp file for input so stdin closes after message
    # Note: Container may exit non-zero when stdin closes (ClosedResourceError), which is expected
    MCP_INPUT=$(mktemp)
    echo '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' > "$MCP_INPUT"

    # Capture output; ignore exit code since stdin close causes expected error
    # Use timeout command if available for better reliability
    if [ -n "$TIMEOUT_CMD" ]; then
        CONTAINER_OUTPUT=$($TIMEOUT_CMD 30 docker run --rm -i \
            --add-host=host.docker.internal:host-gateway \
            -e FREECAD_MODE=xmlrpc \
            -e FREECAD_SOCKET_HOST=host.docker.internal \
            {{image_name}}:test 2>&1 < "$MCP_INPUT" || true)
    else
        CONTAINER_OUTPUT=$(docker run --rm -i \
            --add-host=host.docker.internal:host-gateway \
            -e FREECAD_MODE=xmlrpc \
            -e FREECAD_SOCKET_HOST=host.docker.internal \
            {{image_name}}:test 2>&1 < "$MCP_INPUT" || true)
    fi
    rm -f "$MCP_INPUT"
    MCP_INPUT=""  # Clear so cleanup doesn't try to delete again

    echo ""
    echo "Step 4: Verifying response..."

    # Track test results
    TEST_PASSED=true

    # Check if we got a valid MCP initialize response
    if echo "$CONTAINER_OUTPUT" | grep -q '"result".*"protocolVersion"'; then
        echo "✓ Container responded with valid MCP initialize response"
    else
        echo "✗ Container failed to respond with valid MCP response"
        TEST_PASSED=false
    fi

    # Check for server info in response
    if echo "$CONTAINER_OUTPUT" | grep -q '"serverInfo".*"freecad-mcp"'; then
        echo "✓ Server identified as freecad-mcp"
    else
        echo "⚠ Warning: Could not confirm server identity"
    fi

    # Check for FreeCAD bridge connection in logs (use grep -E for clearer alternation)
    if echo "$CONTAINER_OUTPUT" | grep -Eq 'FreeCAD bridge connected|FreeCAD.*GUI'; then
        echo "✓ FreeCAD bridge connection logged"
    else
        echo "⚠ Warning: No FreeCAD bridge connection in logs"
    fi

    echo ""
    echo "=========================================="
    echo "Raw container output (last 20 lines):"
    echo "=========================================="
    echo "$CONTAINER_OUTPUT" | tail -20
    echo ""

    # Note: FreeCAD cleanup is handled by the EXIT trap

    echo ""
    echo "=========================================="
    if [ "$TEST_PASSED" = true ]; then
        echo "✓ PASSED: Docker integration test succeeded!"
        echo "  - Container built and ran successfully"
        echo "  - Robust MCP Server responded to initialize request"
    else
        echo "✗ FAILED: Docker integration test had errors"
        echo "  - Review the output above for details"
    fi
    echo "=========================================="

    # Exit with appropriate code
    if [ "$TEST_PASSED" = false ]; then
        exit 1
    fi

# ============================================================================
# GUI Test Container (for CI debugging)
# ============================================================================

# Build the GUI test container (replicates GitHub Actions CI environment)
# Supports both x86_64 and aarch64 architectures (downloads correct AppImage)
build-gui-test:
    docker build -f {{project_root}}/tests/ci-test/Dockerfile.gui-test \
        -t {{gui_test_image}} \
        {{project_root}}

# Run GUI test container interactively for debugging
gui-test-shell:
    docker run --rm -it \
        -v {{project_root}}:/workspace \
        {{gui_test_image}} \
        /bin/bash

# Run the automated GUI tests in the container
gui-test-run:
    docker run --rm -i \
        -v {{project_root}}:/workspace \
        {{gui_test_image}} \
        /usr/local/bin/run-gui-test.sh

# Run GUI test with custom command
gui-test-cmd *args:
    docker run --rm -it \
        -v {{project_root}}:/workspace \
        {{gui_test_image}} \
        {{args}}

# Quick rebuild and test cycle for GUI debugging
gui-test: build-gui-test gui-test-run

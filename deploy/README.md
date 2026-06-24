# Docker Compose Deployment

Run the full **FreeCAD headless + Robust MCP Bridge + MCP Server (HTTP)** stack in
Docker. This is the recommended setup for cloud deployment, CI, and assembly-agent
integration.

For local IDE debugging with stdio, keep using `uv run freecad-mcp` and FreeCAD on
the host—no changes required.

## Architecture

```text
┌──────────────────── deploy compose stack ────────────────────┐
│                                                              │
│  deploy-freecad-1          deploy-freecad-mcp-1              │
│  (freecad-bridge image)  (freecad-robust-mcp image)          │
│  FreeCAD + Bridge        MCP Server (HTTP)                   │
│  XML-RPC :9875  ◄──────── FREECAD_SOCKET_HOST=freecad        │
│  (internal only)         :8000 → host FREECAD_MCP_PORT       │
│                                                              │
│  volume: assembly-data → /app/sessions                       │
└──────────────────────────────────────────────────────────────┘
```

| Service (compose) | Container name (example) | Image                       | Host port                    |
| ----------------- | ------------------------ | --------------------------- | ---------------------------- |
| `freecad`         | `deploy-freecad-1`       | `freecad-bridge:latest`     | none (internal)              |
| `freecad-mcp`     | `deploy-freecad-mcp-1`   | `freecad-robust-mcp:latest` | `FREECAD_MCP_PORT` → 8000    |

**Important:** Compose creates **two separate containers**. The MCP server is **not**
inside the FreeCAD container, and compose does **not** reuse a `docker run` MCP
container from other workflows.

## Files in this directory

| File                             | Purpose                                           |
| -------------------------------- | ------------------------------------------------- |
| `docker-compose.yml`             | Two-service stack definition                      |
| `.env.example`                   | Template for image tags and ports                 |
| `.env`                           | Local overrides (create from example; gitignored) |
| `mcp-http.cursor.json.example`   | Cursor remote MCP config (HTTP)                   |
| `mcp-http.codex.toml.example`    | Codex remote MCP config (HTTP)                    |
| `mcp-stdio.cursor.json.example`  | Cursor local stdio MCP config template            |
| `mcp-stdio.codex.toml.example`   | Codex local stdio MCP config template             |
| `mcp-stdio.mcp.json.example`     | Claude Code project `.mcp.json` template (stdio)  |
| `mcp-http.mcp.json.example`      | Claude Code project `.mcp.json` template (HTTP)   |

## Prerequisites

- Docker and Docker Compose v2
- Enough disk space for `freecad-bridge` (~6 GB; includes FreeCAD AppImage)
- Ports on the host planned to avoid conflicts (see [Port planning](#port-planning))

## Quick start (full stack)

From the repository root:

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env if needed (image tags, FREECAD_MCP_PORT)

# Build MCP image (bridge image may already exist from a prior build)
docker compose -f deploy/docker-compose.yml build freecad-mcp

# Start in background
docker compose -f deploy/docker-compose.yml up -d

# Check status
docker compose -f deploy/docker-compose.yml ps
```

### MCP endpoints

| Client location                         | URL                                       |
| --------------------------------------- | ----------------------------------------- |
| Host (Cursor, curl, browser)            | `http://localhost:<FREECAD_MCP_PORT>/mcp` |
| Another service in same compose network | `http://freecad-mcp:8000/mcp`             |

Default `FREECAD_MCP_PORT` in `.env.example` is `8000`. Change it if that port is
already in use (for example `8002`).

## Build `freecad-bridge` only

Build and tag the bridge image **without** starting compose. Useful when you want
to validate FreeCAD + Bridge before pulling up the full stack.

```bash
# From repository root
docker build -f Dockerfile.freecad-bridge -t freecad-bridge:latest .

# Optional: pin FreeCAD AppImage version (must match Python 3.11 / FreeCAD 1.1.x)
docker build -f Dockerfile.freecad-bridge \
  --build-arg FREECAD_TAG=1.1.1 \
  -t freecad-bridge:latest .
```

First build downloads the FreeCAD AppImage and may take several minutes.

### Smoke test (standalone bridge)

```bash
docker run -d --name freecad-bridge-test -p 9875:9875 freecad-bridge:latest

# Wait 30–90s, then:
curl -sf -X POST -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?><methodCall><methodName>ping</methodName></methodCall>' \
  http://localhost:9875

docker logs -f freecad-bridge-test
docker rm -f freecad-bridge-test
```

**Note:** `-p 9875:9875` maps bridge to the host and **conflicts** with a FreeCAD
Bridge already running on Windows/macOS/Linux at `localhost:9875`. For compose,
the `freecad` service does **not** publish 9875 to the host—only this standalone
test needs the port mapping.

### How the Docker image installs Bridge (vs FreeCAD wiki)

The [Robust MCP Bridge wiki](https://wiki.freecad.org/Robust_MCP_Bridge_Workbench/en)
describes manual install: download a GitHub release into
`~/.local/share/FreeCAD/Mod/FreecadRobustMCPBridge/`.

The Docker image instead:

1. Installs FreeCAD from the official **AppImage** (via `tests/ci-test/setup-freecad.sh`)
2. **Copies** `freecad/RobustMCPBridge/` from this repository to `/opt/RobustMCPBridge/`
3. Starts headless with:
   `freecadcmd /opt/RobustMCPBridge/freecad_mcp_bridge/blocking_bridge.py`

This skips the Mod directory because headless mode only needs `blocking_bridge.py`
(which imports the bridge server directly). GUI workbench features (toolbar,
preferences UI) are not used in the container.

## Configuration (`deploy/.env`)

```bash
cp deploy/.env.example deploy/.env
```

| Variable               | Default                     | Description                                           |
| ---------------------- | --------------------------- | ----------------------------------------------------- |
| `FREECAD_BRIDGE_IMAGE` | `freecad-bridge:latest`     | Bridge image tag (use local build or registry)        |
| `FREECAD_MCP_IMAGE`    | `freecad-robust-mcp:latest` | MCP server image tag                                  |
| `FREECAD_TAG`          | `1.1.1`                     | FreeCAD AppImage version when **building** bridge     |
| `FREECAD_MCP_PORT`     | `8000`                      | Host port mapped to MCP HTTP `:8000` inside container |

Set `FREECAD_BRIDGE_IMAGE=freecad-bridge:latest` after a local `docker build` so
compose reuses the image instead of rebuilding.

`.env` is only read by **docker compose**, not by `docker build -f Dockerfile.freecad-bridge`.

## Common commands

All commands run from the **repository root**.

```bash
# --- Lifecycle ---
docker compose -f deploy/docker-compose.yml up -d      # start (detached)
docker compose -f deploy/docker-compose.yml up --build # rebuild + start (foreground)
docker compose -f deploy/docker-compose.yml down         # stop and remove containers
docker compose -f deploy/docker-compose.yml down -v    # also remove assembly-data volume

# --- Status and logs ---
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f
docker compose -f deploy/docker-compose.yml logs -f freecad
docker compose -f deploy/docker-compose.yml logs -f freecad-mcp

# --- Build ---
docker compose -f deploy/docker-compose.yml build              # both services
docker compose -f deploy/docker-compose.yml build freecad      # bridge only
docker compose -f deploy/docker-compose.yml build freecad-mcp  # MCP only

# --- Use pre-built images (no build) ---
docker compose -f deploy/docker-compose.yml up -d --no-build

# --- Bridge image only (no compose) ---
docker build -f Dockerfile.freecad-bridge -t freecad-bridge:latest .
```

### Equivalent `just` commands (MCP server only)

These target the **MCP server image** on Docker Hub naming; they do **not** build
`freecad-bridge` or run compose:

```bash
just docker::build          # build freecad-robust-mcp image
just docker::run-http       # MCP HTTP container → host FreeCAD (route 3 style)
just docker::test           # integration test vs host bridge
```

## Deployment modes compared

| Mode | FreeCAD + Bridge | MCP transport | Typical MCP URL |
| ---- | ---------------- | ------------- | ---------------- |
| Local stdio | Windows / host GUI | stdio | `.cursor/mcp.json` `command:` |
| Route 3 (`just docker::run-http`) | Host (`host.docker.internal:9875`) | HTTP | `http://localhost:<port>/mcp` |
| **Compose (this doc)** | `deploy-freecad-1` container | HTTP | `http://localhost:<FREECAD_MCP_PORT>/mcp` |

Route 3 and compose each run their **own** MCP container. They do not share a
container. You can run both if host ports differ.

## Port planning

| Port | Typical use |
| ---- | ----------- |
| `9875` | XML-RPC Bridge on **host** (Windows FreeCAD, route 3) |
| `8000` | Often used by other services; MCP **inside** compose container |
| `8001` | Example: route-3 `freecad-mcp-http` on host |
| `8002` | Example: compose `FREECAD_MCP_PORT` when 8000/8001 are taken |

Compose **does not** publish bridge port `9875` to the host, so Docker full stack
and Windows Bridge on `localhost:9875` can run at the same time.

## MCP client examples

**Cursor** (`deploy/mcp-http.cursor.json.example`):

```json
{
  "mcpServers": {
    "freecad": {
      "url": "http://localhost:8002/mcp",
      "transport": "streamable-http"
    }
  }
}
```

Replace `8002` with your `FREECAD_MCP_PORT`.

**Assembly agent** (same compose network): use `http://freecad-mcp:8000/mcp` and
mount the `assembly-data` volume at the same path as the `freecad` service
(`/app/sessions`).

## Troubleshooting

### `deploy-freecad-1` unhealthy

- First start can take 30–90 seconds while FreeCAD initializes.
- Check logs: `docker compose -f deploy/docker-compose.yml logs freecad`
- Rebuild bridge after code changes:
  `docker build -f Dockerfile.freecad-bridge -t freecad-bridge:latest .`

### MCP cannot connect to FreeCAD

- Ensure `FREECAD_BRIDGE_BIND_HOST=0.0.0.0` in the bridge image (set in
  `Dockerfile.freecad-bridge`).
- In compose, MCP must use `FREECAD_SOCKET_HOST: freecad` (service name), not
  `localhost`.

### Port already allocated

- Change `FREECAD_MCP_PORT` in `deploy/.env`.
- Stop conflicting containers: `docker ps` and `docker stop <name>`.

### Two MCP containers confusion

| Container name         | Source                            |
| ---------------------- | --------------------------------- |
| `freecad-mcp-http`     | `just docker::run-http` (route 3) |
| `deploy-freecad-mcp-1` | `docker compose up` (this stack)  |

## Related documentation

- [Installation](../docs/getting-started/installation.md) — MCP server and workbench install
- [Configuration](../docs/getting-started/configuration.md) — environment variables
- [Connection modes](../docs/guide/connection-modes.md) — xmlrpc / socket / embedded
- [Robust MCP Bridge workbench](../docs/guide/workbench.md) — GUI and Addon Manager install

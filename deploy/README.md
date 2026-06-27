# Docker Compose Deployment

Run the full **FreeCAD headless + Robust MCP Bridge + MCP Server (HTTP)** stack in
Docker. This is the recommended setup for cloud deployment, CI, and assembly-agent
integration.

For local IDE debugging with stdio, keep using `uv run freecad-mcp` and FreeCAD on
the host—no changes required.

## Two repositories (corporate split)

| Repository | Jenkins image | Dockerfile |
| ---------- | ------------- | ---------- |
| `service_freecad_bridge` | `hub.designorder.cn/freecad-bridge:dev` | root `Dockerfile` |
| **This repo** (MCP server) | `hub.designorder.cn/freecad-robust-mcp:dev` | root `Dockerfile` |

Both Jenkins jobs use the **dev** branch and are triggered manually. Base images
(`python:3.11-alpine`, `ubuntu:24.04`, etc.) pull from `hub.designorder.cn/` via the
`DOCKER_REGISTRY` build arg.

This repo **does not build** the bridge image. Compose pulls
`FREECAD_BRIDGE_IMAGE` from the registry (or you build it locally in
`service_freecad_bridge`).

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

| Service (compose) | Container name (example) | Image (from `deploy/.env`)     | Host port                 |
| ----------------- | ------------------------ | ------------------------------ | ------------------------- |
| `freecad`         | `deploy-freecad-1`       | `FREECAD_BRIDGE_IMAGE`         | none (internal)           |
| `freecad-mcp`     | `deploy-freecad-mcp-1`   | `FREECAD_MCP_IMAGE`            | `FREECAD_MCP_PORT` → 8000 |

**Important:** Compose creates **two separate containers**. The MCP server is **not**
inside the FreeCAD container.

## Unified image workflow

**One image name** in `deploy/.env` is used for local build, `compose up`, and
registry push. There is no separate `freecad-bridge:latest` vs registry tag — that
causes duplicate images with different IDs.

```text
deploy/.env  →  FREECAD_*_IMAGE (e.g. hub.designorder.cn/freecad-bridge:dev)
       │
       ├─ just docker::compose-build     →  local image with that tag
       ├─ just docker::compose-up        →  run stack (same local image)
       └─ just docker::publish-push-*    →  push same tag to registry
```

### Tag policy

| Tag        | Mutable? | Who uses it                              | How to publish                 |
| ---------- | -------- | ---------------------------------------- | ------------------------------ |
| `dev`      | Yes      | Dev / staging; downstream fast iteration | `just docker::publish-dev`     |
| `X.Y.Z`    | No       | Production                               | `just docker::publish 1.0.0`   |

Downstream compose should **pin `:dev`** (or a semver for production) and update with:

```bash
docker compose pull && docker compose up -d --no-build
```

You overwrite `:dev` on the registry after each iteration; downstream does not change
their `.env`.

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
- `deploy/.env` created from `.env.example` (**required** — compose reads image tags from here)
- Enough disk space for `freecad-bridge` (~6 GB; includes FreeCAD AppImage)
- Docker Hub (or other registry) login if you push images

## Quick start (full stack)

From the repository root:

```bash
cp deploy/.env.example deploy/.env
# Edit FREECAD_*_IMAGE if your registry namespace differs

# Pull bridge + build MCP (tags from deploy/.env)
just docker::compose-pull-bridge
just docker::compose-build-mcp

# Start in background (no rebuild)
just docker::compose-up

# Check status
docker compose -f deploy/docker-compose.yml ps
```

### Publish to a registry (same tags as local)

```bash
# Build + push MCP only (bridge: use service_freecad_bridge Jenkins)
just docker::publish-dev

# Or build locally first, push when ready:
just docker::compose-build-mcp
just docker::publish-push-mcp
```

Show resolved image names and commands:

```bash
just docker::publish-show
```

### MCP endpoints

| Client location                         | URL                                       |
| --------------------------------------- | ----------------------------------------- |
| Host (Cursor, curl, browser)            | `http://localhost:<FREECAD_MCP_PORT>/mcp` |
| Another service in same compose network | `http://freecad-mcp:8000/mcp`             |

Default `FREECAD_MCP_PORT` in `.env.example` is `8000`. Change it if that port is
already in use (for example `8002`).

## Daily development

| Step | Command |
| ---- | ------- |
| Changed MCP server code | `just docker::compose-build-mcp` then `just docker::compose-up` |
| Changed bridge / workbench | Build in `service_freecad_bridge`, push via Jenkins, then `just docker::compose-pull-bridge` |
| Share MCP with downstream | `just docker::publish-push-mcp` |
| Remove old `freecad-*:latest` images | `just docker::clean-legacy-tags` |

**Avoid** `docker compose up --build` with legacy `freecad-*:latest` tags — use
`compose-build` + `compose-up` so the image name always matches `deploy/.env`.

## Configuration (`deploy/.env`)

```bash
cp deploy/.env.example deploy/.env
```

| Variable               | Example                                      | Description                                        |
| ---------------------- | -------------------------------------------- | -------------------------------------------------- |
| `FREECAD_BRIDGE_IMAGE` | `hub.designorder.cn/freecad-bridge:dev`      | Bridge image (built in `service_freecad_bridge`)   |
| `FREECAD_MCP_IMAGE`    | `hub.designorder.cn/freecad-robust-mcp:dev`  | MCP server image (built in **this** repo)          |
| `DOCKER_REGISTRY`      | `hub.designorder.cn/`                        | Base image prefix for `Dockerfile` `FROM` lines    |
| `FREECAD_TAG`          | `1.1.1`                                      | FreeCAD version (bridge repo; informational here)  |
| `FREECAD_MCP_PORT`     | `8000`                                       | Host port mapped to MCP HTTP `:8000` in container  |

Image lines in `deploy/.env` are the **only** place to set registry namespace and tag.
Downstream repos copy the same `FREECAD_*_IMAGE` values.

## Common commands

All commands run from the **repository root**. Prefer `just` wrappers — they read
`deploy/.env` automatically.

```bash
# --- Build / pull (tags from deploy/.env) ---
just docker::compose-pull-bridge        # pull bridge from registry
just docker::compose-build-mcp          # build MCP in this repo
just docker::compose-build              # alias: build MCP only

# --- Run ---
just docker::compose-up                   # start detached, no rebuild
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml down -v   # also remove volume

# --- Publish MCP to registry (same tag as local) ---
just docker::publish-dev                  # build + push MCP
just docker::publish-push-mcp             # push MCP only (after compose build)
just docker::publish 1.0.0                # immutable MCP release tag

# --- Logs ---
docker compose -f deploy/docker-compose.yml logs -f
docker compose -f deploy/docker-compose.yml logs -f freecad
docker compose -f deploy/docker-compose.yml logs -f freecad-mcp
```

### Downstream compose

Use the **same** `FREECAD_*_IMAGE` values in their `.env`:

```bash
FREECAD_BRIDGE_IMAGE=hub.designorder.cn/freecad-bridge:dev
FREECAD_MCP_IMAGE=hub.designorder.cn/freecad-robust-mcp:dev
```

Update after you push:

```bash
docker compose pull && docker compose up -d --no-build
```

## Bridge smoke test (standalone, optional)

Uses the image tag from `deploy/.env` (not a separate `freecad-bridge:latest`):

```bash
set -a && source deploy/.env && set +a
docker run -d --name freecad-bridge-test -p 9875:9875 "${FREECAD_BRIDGE_IMAGE}"

# Wait 30–90s, then:
curl -sf -X POST -H "Content-Type: text/xml" \
  -d '<?xml version="1.0"?><methodCall><methodName>ping</methodName></methodCall>' \
  http://localhost:9875

docker rm -f freecad-bridge-test
```

**Note:** `-p 9875:9875` conflicts with a bridge on the host. Compose does **not**
publish 9875 to the host.

### How the Docker image installs Bridge (vs FreeCAD wiki)

The [Robust MCP Bridge wiki](https://wiki.freecad.org/Robust_MCP_Bridge_Workbench/en)
describes manual install via Addon Manager. The Docker image instead:

1. Installs FreeCAD from the official **AppImage** (via `tests/ci-test/setup-freecad.sh`)
2. **Copies** `freecad/RobustMCPBridge/` from this repository to `/opt/RobustMCPBridge/`
3. Starts headless with:
   `freecadcmd /opt/RobustMCPBridge/freecad_mcp_bridge/blocking_bridge.py`

## Deployment modes compared

| Mode | FreeCAD + Bridge | MCP transport | Typical MCP URL |
| ---- | ---------------- | ------------- | ---------------- |
| Local stdio | Windows / host GUI | stdio | `.cursor/mcp.json` `command:` |
| Route 3 (`just docker::run-http`) | Host (`host.docker.internal:9875`) | HTTP | `http://localhost:<port>/mcp` |
| **Compose (this doc)** | `deploy-freecad-1` container | HTTP | `http://localhost:<FREECAD_MCP_PORT>/mcp` |

Route 3 uses `just docker::build` (local `freecad-robust-mcp` image only) — **not**
the compose stack workflow. Route 3 and compose each run their **own** MCP container.

## Port planning

| Port | Typical use |
| ---- | ----------- |
| `9875` | XML-RPC Bridge on **host** (Windows FreeCAD, route 3) |
| `8000` | Often used by other services; MCP **inside** compose container |
| `8001` | Example: route-3 `freecad-mcp-http` on host |
| `8002` | Example: compose `FREECAD_MCP_PORT` when 8000/8001 are taken |

Compose **does not** publish bridge port `9875` to the host.

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
mount the `assembly-data` volume at `/app/sessions`.

## Troubleshooting

### `deploy-freecad-1` unhealthy

- First start can take 30–90 seconds while FreeCAD initializes.
- Check logs: `docker compose -f deploy/docker-compose.yml logs freecad`
- Rebuild bridge: push from `service_freecad_bridge` Jenkins, then
  `just docker::compose-pull-bridge` and `just docker::compose-up`

### MCP cannot connect to FreeCAD

- Ensure `FREECAD_BRIDGE_BIND_HOST=0.0.0.0` in the bridge image (set in
  `service_freecad_bridge` root `Dockerfile`).
- In compose, MCP must use `FREECAD_SOCKET_HOST: freecad` (service name), not
  `localhost`.

### Duplicate images in Docker Desktop

- Use only `deploy/.env` tags (`hub.designorder.cn/...:dev`), not `freecad-*:latest`.
- Run `just docker::clean-legacy-tags` to remove legacy local tags.

### Port already allocated

- Change `FREECAD_MCP_PORT` in `deploy/.env`.
- Stop conflicting containers: `docker ps` and `docker stop <name>`.

### Two MCP containers confusion

| Container name         | Source                            |
| ---------------------- | --------------------------------- |
| `freecad-mcp-http`     | `just docker::run-http` (route 3) |
| `deploy-freecad-mcp-1` | `just docker::compose-up`         |

## Related documentation

- [Installation](../docs/getting-started/installation.md) — MCP server and workbench install
- [Configuration](../docs/getting-started/configuration.md) — environment variables
- [Connection modes](../docs/guide/connection-modes.md) — xmlrpc / socket / embedded
- [Robust MCP Bridge workbench](../docs/guide/workbench.md) — GUI and Addon Manager install

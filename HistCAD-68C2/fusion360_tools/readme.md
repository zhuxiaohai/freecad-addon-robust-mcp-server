# fusion360_tools

This is the Fusion 360 side of the public JSON-to-STEP/F3D exporter and editability benchmark.

## What is included

- `server/fusion360_server.py`: Fusion add-in entry point that starts the local HTTP command server.
- `server/command_runner.py`: Dispatches JSON commands to the CAD implementation.
- `server/CAD/*.py`: Fusion API implementation for sketches, constraints, solid operations, validation, and STEP/F3D export.
- `server/launch.py`: Optional desktop launcher/ping/detach helper.
- `client/fusion360_client.py`: Python HTTP client used by the exporter and launcher.

Generated logs, `__pycache__`, local launch state, and project-specific sync scripts are intentionally not included.

## Install the Fusion add-in on Windows

From the `json_to_step` folder:

```powershell
.\scripts\install_fusion_addin.ps1
```

This copies `fusion360_tools\server` to:

```text
%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\fusion360_server
```

Then open Fusion 360 and run `fusion360_server` from `Utilities > Scripts and Add-Ins > Add-Ins`.

## Start/check the server from Python

After installing this package with `python -m pip install -e .`, you can use:

```powershell
fusion360-server-launch --host 127.0.0.1 --start_port 8080 --instances 1
fusion360-server-launch --ping
```

If Fusion is not found automatically, set one of these before launching:

```powershell
$env:FUSION360_LAUNCHER="C:\Path\To\FusionLauncher.exe"
# or
$env:FUSION360_EXECUTABLE="C:\Path\To\Fusion360.exe"
```

The launcher writes transient launch state to the OS temp directory, or to `$env:FUSION360_LAUNCH_JSON` when that variable is set.

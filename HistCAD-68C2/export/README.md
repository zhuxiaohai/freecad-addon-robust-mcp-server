# STEP and F3D Export

This document describes how to export HistCAD operation JSON files to STEP or F3D files through Autodesk Fusion 360.

```text
input .json file       -> output .step or .f3d file
input directory/*.json -> output directory/*.step or directory/*.f3d
```

## Install

From this folder:

```powershell
python -m pip install -e .
```

Runtime requirements:

- Python 3.10+
- `requests`
- `scipy`
- Autodesk Fusion 360 with the bundled HTTP command server add-in running

## Install the Fusion 360 Add-in

```powershell
.\scripts\install_fusion_addin.ps1
```

The helper copies `fusion360_tools\server` to Fusion 360's AddIns directory. Open Fusion 360 and run:

```text
Utilities > Scripts and Add-Ins > Add-Ins > fusion360_server > Run
```

## Start or Check the Server

```powershell
python -m fusion360_tools.server.launch --ping
```

To launch Fusion and prepare the endpoint:

```powershell
python -m fusion360_tools.server.launch --host 127.0.0.1 --start_port 8080 --instances 1
python -m fusion360_tools.server.launch --ping
```

If Fusion is not found automatically, set one of these:

```powershell
$env:FUSION360_LAUNCHER="C:\Path\To\FusionLauncher.exe"
$env:FUSION360_EXECUTABLE="C:\Path\To\Fusion360.exe"
```

## Export Files

Single STEP file:

```powershell
python -m export input.json output.step --host 127.0.0.1 --port 8080
```

Single F3D file:

```powershell
python -m export.export_f3d input.json output.f3d --host 127.0.0.1 --port 8080
```

Directory export, preserving relative paths:

```powershell
python -m export input_json_dir output_step_dir --host 127.0.0.1 --port 8080
python -m export.export_f3d input_json_dir output_f3d_dir --host 127.0.0.1 --port 8080
```

Resume an interrupted folder export:

```powershell
python -m export input_json_dir output_step_dir --skip-existing
python -m export.export_f3d input_json_dir output_f3d_dir --skip-existing
```

## Test with the Bundled Example

With the Fusion 360 server running, export `examples\test1.json` to both STEP and F3D:

```powershell
python -m export.test_example --host 127.0.0.1 --port 8080
```

Equivalent explicit commands:

```powershell
python -m export .\examples\test1.json .\outputs\export_test1\test1.step --host 127.0.0.1 --port 8080
python -m export.export_f3d .\examples\test1.json .\outputs\export_test1\test1.f3d --host 127.0.0.1 --port 8080
```

## Python API

```python
from export import ExportOptions, export_json_tree, export_json_tree_to_f3d

step_result = export_json_tree(
    "input_json_dir",
    "output_step_dir",
    options=ExportOptions(host="127.0.0.1", port=8080),
)

f3d_result = export_json_tree_to_f3d(
    "input_json_dir",
    "output_f3d_dir",
    options=ExportOptions(host="127.0.0.1", port=8080),
)
```

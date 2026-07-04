# STEP/F3D 导出

本文档说明如何通过 Autodesk Fusion 360 将 HistCAD operation JSON 导出为 STEP 或 F3D 文件。

```text
输入 .json 文件          -> 输出 .step 或 .f3d 文件
输入目录/*.json         -> 输出目录/*.step 或 输出目录/*.f3d
```

## 安装

```powershell
python -m pip install -e .
```

运行时依赖：

- Python 3.10+
- `requests`
- `scipy`
- Autodesk Fusion 360，并开启随包提供的 HTTP 命令服务器插件

## 安装 Fusion 360 插件

```powershell
.\scripts\install_fusion_addin.ps1
```

脚本会将 `fusion360_tools\server` 复制到 Fusion 360 的 AddIns 目录。然后在 Fusion 360 中运行：

```text
Utilities > Scripts and Add-Ins > Add-Ins > fusion360_server > Run
```

## 启动或检查服务器

```powershell
python -m fusion360_tools.server.launch --ping
```

启动 Fusion 并准备端口：

```powershell
python -m fusion360_tools.server.launch --host 127.0.0.1 --start_port 8080 --instances 1
python -m fusion360_tools.server.launch --ping
```

如果脚本无法自动定位 Fusion 360，请设置路径：

```powershell
$env:FUSION360_LAUNCHER="C:\Path\To\FusionLauncher.exe"
$env:FUSION360_EXECUTABLE="C:\Path\To\Fusion360.exe"
```

## 导出文件

导出单个 STEP：

```powershell
python -m export input.json output.step --host 127.0.0.1 --port 8080
```

导出单个 F3D：

```powershell
python -m export.export_f3d input.json output.f3d --host 127.0.0.1 --port 8080
```

导出整个目录并保持相对路径：

```powershell
python -m export input_json_dir output_step_dir --host 127.0.0.1 --port 8080
python -m export.export_f3d input_json_dir output_f3d_dir --host 127.0.0.1 --port 8080
```

断点续传：

```powershell
python -m export input_json_dir output_step_dir --skip-existing
python -m export.export_f3d input_json_dir output_f3d_dir --skip-existing
```

## 使用随附样例测试

启动 Fusion 360 服务器后，将 `examples\test1.json` 导出为 STEP 和 F3D：

```powershell
python -m export.test_example --host 127.0.0.1 --port 8080
```

等价的显式命令：

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

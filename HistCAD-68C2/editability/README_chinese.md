# 约束可编辑性基准

本文档说明如何使用 Fusion 360 工具运行约束可编辑性基准、刷新指标，并生成可视化对比图。

该基准会从 HistCAD JSON 中采样参数编辑意图，在不同约束保留策略下重放编辑，并统计编辑后的 CAD 建模历史是否可以重建且保持未编辑约束。

## 输出文件

每次运行会在指定输出目录中写入以下文件：

- `manifest.json`：选中的样本与编辑意图
- `results.csv`：逐样本基准结果
- `details.json`：详细评估记录
- `summary.json`：汇总指标
- `.constraint_editability_checkpoint.json`：断点续跑检查点

## 指标

- `ER`：编辑可达率。目标编辑被命中，草图验证通过，并且编辑后的建模历史可以重建。
- `cPCSR`：条件保留约束满足率。在编辑可达的样本中，未编辑约束仍被满足的比例。
- `OES`：整体可编辑成功率。按当前指标定义，`OES = ER x cPCSR`。

## 运行基准

先在 Fusion 360 中启动随附的 HTTP 命令服务器插件，然后运行：

```powershell
python -m editability.experiment `
  --dataset-root path\to\histcad_json_root `
  --output-dir outputs\constraint_editability\run_001 `
  --sample-size 20 `
  --host 127.0.0.1 --port 8080 `
  --skip-visualization
```

## 使用随附样例测试

启动 Fusion 360 服务器后，使用 `examples\test1.json` 运行一个样本的可编辑性检查：

```powershell
python -m editability.test_example --host 127.0.0.1 --port 8080
```

等价的显式命令：

```powershell
python -m editability.experiment `
  --dataset-root .\examples `
  --output-dir .\outputs\editability_test1 `
  --sample-size 1 `
  --intent-families modify_dimension `
  --host 127.0.0.1 --port 8080 `
  --restart-every 0 `
  --skip-visualization
```

## 从缓存结果刷新指标

以下命令从已有结果目录读取 `results.csv` 和 `details.json`，并重新写入 `summary.json`：

```powershell
python -m editability.metrics `
  --output-dir outputs\constraint_editability\run_001 `
  --summary-only
```

## 可视化

先安装可视化依赖：

```powershell
python -m pip install -e .[visualization]
```

然后从基准结果目录生成样本级对比图：

```powershell
python -m editability.visualization `
  --result-dir outputs\constraint_editability\run_001 `
  --output-dir outputs\constraint_editability\run_001\visualization `
  --host 127.0.0.1 --port 8080
```

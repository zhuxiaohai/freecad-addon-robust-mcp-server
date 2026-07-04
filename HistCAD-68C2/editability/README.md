# Constraint Editability Benchmark

This document describes the constraint editability benchmark tools included with the Fusion 360 exporter.

The benchmark samples parameter-edit intents, replays each edit on two branches, and reports whether the edited CAD history rebuilds while preserving constraints.

## Outputs

Each run writes these files into the selected output directory:

- `manifest.json`: selected samples and edit intents
- `results.csv`: row-level benchmark outcomes
- `details.json`: detailed evaluation records
- `summary.json`: aggregate metrics
- `.constraint_editability_checkpoint.json`: resume checkpoint

## Metrics

- `ER`: edit reachability. The target edit is hit, the sketch validates, and the edited history rebuilds.
- `cPCSR`: conditional preserved constraint satisfaction rate among reachable cases.
- `OES`: overall editable success. Under the current metric definition, `OES = ER x cPCSR`.

## Run the Benchmark

Start Fusion 360 with the bundled server add-in, then run:

```powershell
python -m editability.experiment `
  --dataset-root path\to\histcad_json_root `
  --output-dir outputs\constraint_editability\run_001 `
  --sample-size 20 `
  --host 127.0.0.1 --port 8080 `
  --skip-visualization
```

## Test with the Bundled Example

With the Fusion 360 server running, run a one-sample check against `examples\test1.json`:

```powershell
python -m editability.test_example --host 127.0.0.1 --port 8080
```

Equivalent explicit command:

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

## Refresh Metrics from Cached Outputs

This command reads `results.csv` and `details.json` from an existing result directory and rewrites `summary.json`:

```powershell
python -m editability.metrics `
  --output-dir outputs\constraint_editability\run_001 `
  --summary-only
```

## Visualization

Install optional visualization dependencies first:

```powershell
python -m pip install -e .[visualization]
```

Then generate comparison panels from a benchmark result directory:

```powershell
python -m editability.visualization `
  --result-dir outputs\constraint_editability\run_001 `
  --output-dir outputs\constraint_editability\run_001\visualization `
  --host 127.0.0.1 --port 8080
```

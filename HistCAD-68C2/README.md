

# HistCAD


This repository also includes Fusion 360 utilities for working with HistCAD JSON files:

- [STEP/F3D export guide](export/README.md)
- [Constraint editability benchmark guide](editability/README.md)

---

## 📁 Dataset Structure

This public release contains the **DeepCAD** and **Fusion360** splits. (The Industrial split is temporarily reserved for a future release).

The dataset is packaged as follows:

```text
HistCAD_release/
├── JSON/                            # CAD modeling histories (.json)
│   ├── histcad_sequences_deepcad_0001-0099.zip
│   └── histcad_sequences_fusion360_0100.zip
├── STEP/                            # 3D geometry exports (.step)
│   ├── histcad_step_deepcad_0001-0099.zip
│   └── histcad_step_fusion360_0100.zip
└── annotations/
    └── histcad_annotations.csv      # Text descriptions and captions

```

- **ID Convention**: Folder prefixes `0001`-`0099` belong to the DeepCAD split, and `0100` belongs to the Fusion360 split.
- **File Matching**: Files across different modalities share an identical unique identifier (uid) (e.g., `0001/00010008.json` maps directly to `0001/00010008.step`).

---

## 🛠️ Supported Downstream Tasks

### 1. Text-to-CAD Generation

- **Application**: Train models to generate CAD modeling histories (JSON) directly from natural language prompts.
- **Usage**: Pair the text descriptions from `histcad_annotations.csv` with the corresponding target files in the `JSON/` directory.

### 2. CAD Reconstruction

- **Application**: Predict the full, editable parametric modeling sequence from raw 3D geometry.
- **Usage**: Use the `.step` files as inputs to train your model to reconstruct the step-by-step CAD histories in the `.json` files.

### 3. Parametric Editing & Rebuild Evaluation

- **Application**: Evaluate how well a CAD model handles parameter updates (e.g., resizing a dimension) without breaking the structure.
- **Metrics**: Test your models using our Constraint-Aware Editability Benchmark:
- **ER (Edit Reachability)**: Can the modified sequence successfully compile and rebuild into a valid 3D shape?
- **cPCSR (conditional Preserved Constraint Satisfaction Rate)**: Do unedited constraints (like parallelism or perpendicularity) remain valid after editing?
- **OES (Overall Editable Success)**: Strict overall success rate ($OES = ER \times cPCSR$).


---

## 🏷️ Annotation Format (`histcad_annotations.csv`)

The CSV file provides text descriptions at different granularities for each sample:

| Column | Description |
| --- | --- |
| `uid` | Unique identifier (`subset_id/sample_id`) |
| `Modeling Process` | Step-by-step description of how the CAD model was built |
| `Geometric Feature` | Summary of the shapes, holes, and symmetry |
| `Functional Type` | The semantic or engineering category of the part (e.g., "hex nut") |
| `NLT` | Structured description combining geometric and text features for LLM workflows |

---

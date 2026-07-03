---
license: mit
---
# HistCAD Dataset

HistCAD is a constraint-aware parametric history-based CAD representation, dataset, and benchmark for editable CAD generation. It records executable modeling histories with explicit sketch constraints, feature operations, and boundary references through 3D points so parameter edits can be evaluated for design-intent preservation.

This repository/package contains a public release subset of HistCAD, including parametric JSON sequences, STEP exports, rendered images, and text annotations.


---

## Overview

HistCAD represents CAD models as executable, constraint-aware parametric histories. Compared with sequence-only CAD datasets, HistCAD explicitly exposes sketch geometry, geometric constraints, feature operations, and boundary references in a unified, software-independent history format.


The full dataset contains:

- **HistCAD-DeepCAD**: 153,534 executable sequences aligned from DeepCAD and SketchGraphs *(Available)*
- **HistCAD-Fusion360**: 8,609 executable sequences recovered from Fusion 360 Gallery designs *(Available)*
- **HistCAD-Industrial**: 8,093 professionally authored industrial standard-part sequences *(Coming soon)*
- **Total Full Corpus**: 170,236 executable modeling sequences

This release contains the academic partitions of the full HistCAD collection.

---

## Subset IDs

In the current release convention, subset IDs map to the three HistCAD partitions as follows:

- 0001-0099: **HistCAD-DeepCAD** *(Active)*
- 0100: **HistCAD-Fusion360** *(Active)*
- 0101: **HistCAD-Industrial** *(Reserved for future release)*

HistCAD-DeepCAD and HistCAD-Fusion360 together form the academic portion of HistCAD.

---

## Package Contents

This release contains four data modalities:

1. **Parametric modeling sequences** in JSON format
2. **STEP files** exported from executable CAD histories
3. **Rendered images** from multiple canonical viewpoints
4. **Text annotations** in CSV format

A typical packaged release layout is structured as follows:

```text
HistCAD_release/
|-- JSON/
|   |-- histcad_sequences_deepcad_0001-0099.zip
|   -- histcad_sequences_fusion360_0100.zip
|-- STEP/
|   |-- histcad_step_deepcad_0001-0099.zip
|   -- histcad_step_fusion360_0100.zip
-- annotations/
    -- histcad_annotations.csv

```

Each zip stores files directly from the subset-relative path, without an extra HistCAD/sequences or HistCAD/step prefix. When multiple modalities are available for the same sample, they share the same sample stem. For example:

- Inside histcad_sequences_deepcad_0001-0099.zip: 0001/00010008.json
- Inside histcad_step_deepcad_0001-0099.zip: 0001/00010008.step
- Inside histcad_rendered_images_iso_front_right_top.zip: 0001/00010008_iso_front_right_top_.png

---

## JSON Sequence Format

Each JSON file stores a **parametric modeling history** as a list of ordered operations. The format is designed to be independent of any specific CAD kernel: sketch entities and constraints are represented directly, and operations that depend on existing boundaries use 3D reference points rather than backend-specific topology identifiers. A modeling sequence contains one or more features defined by the following operations:

### 1. Extrude

```json
{
  "coordinate_system": {...},
  "sketch": {...},
  "constraints": {...},
  "towards": 6.0,
  "opposite": 0.0,
  "operation": "NewBody"
}

```

#### 1.1 coordinate_system

The local coordinate system where the sketch lies, defining the spatial position and orientation of the sketch plane.

- **Euler Angles**: A 3-element array [α, β, γ] representing rotations around the X, Y, and Z axes (in degrees).
- **Translation Vector**: A 3-element array [x, y, z] representing the origin position of the sketch relative to the world coordinate system.

#### 1.2 sketch (Sketch Geometric Entities)

Contains 2D geometric primitives such as lines (line_), circles (circle_), and arcs (arc_).

- **Line**:

```json
"line_1": { "start": [x1, y1], "end": [x2, y2] }
```

- **Circle**:

```json
"circle_1": { "center": [x, y], "radius": r }
```
  
- **Ellipse**:

```json
"ellipse_1": {
    "center": [x, y],  // Center point
    "major": r1,       // Major radius (semi-major axis)
    "minor": r2,       // Minor radius (semi-minor axis)
    "angle": deg       // Rotation angle of major axis relative to local X-axis (degrees)
}
```

- **Arc**:

```json
"arc_1": { "start": [x1, y1], "middle": [x2, y2], "end": [x3, y3] }
```
  

- **Elliptical Arc**:

```json
"elliptical_arc_1": {
    "start": [x1, y1], "end": [x2, y2],
    "major": r1, "minor": r2,
    "angle": deg,           // Rotation angle of major axis relative to local X-axis
    "large_arc": false,     // Whether to use the arc greater than 180 degrees
    "sweep": true           // Drawing direction: true=counter-clockwise (mathematically positive)
}
```

- **B-Spline (NURBS)**:

```json
"nurbs_1": {
    "degree": 3,            // Degree (typically 3)
    "periodic": false,      // Whether the curve is periodic
    "controls": [[x1, y1], [x2, y2], ...], // List of control points
    "weights": [1.0, 1.0, ...],  // List of weights. If omitted, defaults to all 1.0 (non-rational)
    "knots": [0.0, 0.0, ...]     // Knot vector (length = number of control points + degree + 1)
}
```
  

#### 1.3 constraints

HistCAD explicitly supports 19 geometric constraint types:
Perpendicular, Parallel, Horizontal, Vertical, Equal, Tangent, Normal, Coincident, Concentric, Fix, Midpoint, Mirror, Angle, Diameter, Radius, MajorRadius, MinorRadius, Distance, Length.

#### Reference Conventions

- entity_ref: Refers to a geometric entity, e.g., "line_3", "circle_1", "arc_2".
- point_ref: Refers to a specific point on an entity, e.g., "line_3.start", "line_3.end", "circle_1.center", "arc_2.center".
- value_expr: A dimension expression string, e.g., "8 mm", ".625 in", "0.24999999697870154*in". It can also be a raw numerical value (defaults to mm), e.g., 3.14.
- param_dict: A dictionary containing constraint metadata parameters, e.g., {"length":"3 mm", "direction":"MINIMUM"}.

```json
"constraints": {
  "Coincident": [["line_1.end", "line_2.start"]],
  "Horizontal": ["line_4", ["circle_1.center", "circle_2.center"]],
  "Length": [["line_4", "8 mm"]],
  "Distance": [["line_1", "line_2", {"direction": "MINIMUM", "length": "3 mm"}]]
}

```

#### Format Specifications and Examples for the 19 Constraints

1. **Perpendicular**: Two lines are perpendicular. Format: [entity_ref, entity_ref]. Example: ["line_1", "line_4"].
2. **Parallel**: Two lines are parallel. Format: [entity_ref, entity_ref]. Example: ["line_1", "line_5"].
3. **Horizontal**: Horizontal constraint. Format A: entity_ref (a single line is horizontal). Format B: [point_ref, point_ref] (two points are horizontally aligned). Example: "line_4" or ["circle_1.center", "circle_2.center"].
4. **Vertical**: Vertical constraint. Format A: entity_ref (a single line is vertical). Format B: [point_ref, point_ref] (two points are vertically aligned). Example: "line_2" or ["line_1.start", "line_4.end"].
5. **Equal**: Equal geometric dimensions (e.g., line lengths, circle/arc radii). Format: [entity_ref, entity_ref]. Example: ["line_2", "line_3"], ["circle_1", "circle_2"].
6. **Tangent**: Two entities are tangent. Format: [entity_ref, entity_ref] (combinations include arc-line, arc-arc, circle-circle, circle-line). Example: ["arc_1", "line_1"].
7. **Normal**: Normal relationship between a line and a circle/arc. Format: [entity_ref, entity_ref]. Example: ["arc_1", "line_2"].
8. **Coincident**: Points are coincident (topological connection). Format: [point_ref, point_ref]. Example: ["line_1.end", "line_2.start"].
9. **Concentric**: Circles or arcs share the same center point. Format: [entity_ref, entity_ref]. Example: ["arc_1", "circle_3"].
10. **Fix**: Fixes a geometric entity or point position. Format A: entity_ref. Format B: point_ref. Example: "line_1" or "circle_1.center".
11. **Midpoint**: Midpoint relationship. Format A: [mid_point_ref, [point_ref_a, point_ref_b]] (the former is the midpoint of the latter two points). Format B: [point_ref, entity_ref] (the point is the midpoint of a line or arc segment). Example: ["arc_1.center", "line_1"].
12. **Mirror**: Symmetric mirror relation about an axis line (the second element is the mirror axis). Format: [entity_ref, axis_line_ref, entity_ref] or point pairs. Example: ["line_10", "line_8", "line_1"].
13. **Angle**: Directed angle between two lines. Format: [entity_ref, entity_ref, angle_deg] where angle_deg is a numerical value (0-360). Example: ["line_1", "line_2", 200.0].
14. **Diameter**: Diameter dimensioning for a circle/arc. Format: [entity_ref, value_expr]. Example: ["circle_1", "78 mm"].
15. **Radius**: Radius dimensioning for a circle/arc. Format: [entity_ref, value_expr]. Example: ["arc_1", ".625 in"].
16. **MajorRadius**: Major radius dimensioning for an ellipse. Format: [entity_ref, value_expr].
17. **MinorRadius**: Minor radius dimensioning for an ellipse. Format: [entity_ref, value_expr].
18. **Length**: Length dimensioning for a line segment. Format: [entity_ref, value_expr]. Example: ["line_4", "8 mm"].
19. **Distance**: Distance dimensioning between entities (points, lines, circles, or arcs). Format: [ref_a, ref_b, param_dict]. param_dict contains the distance value length, the direction alignment direction (MINIMUM / HORIZONTAL / VERTICAL), and optional half-space side attributes halfSpace0/halfSpace1. Example: ["line_3.end", "line_2.end", {"direction":"VERTICAL","length":"5*inch"}].

#### 1.4 towards and opposite

- **towards**: Extrusion distance along the positive sketch normal direction (numerical value).
- **opposite**: Extrusion distance along the negative sketch normal direction (numerical value).

#### 1.5 operation (Boolean Operation)

- Defines the body generation mode, which must be one of the following strings:
- "NewBody": Creates a new independent solid body.
- "Join": Merges/unions the volume into an existing body.
- "Cut": Subtracts the volume from an existing body.
- "Intersect": Keeps only the intersecting volume.


---

### 2. Revolve

```json
{
  "coordinate_system": {...},
  "sketch": {...},
  "constraints": {...},
  "start": 0.0,
  "end": 180.0,
  "axis": [[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
  "operation": "NewBody"
}

```

- **start and end**: The start and end angles of the revolution. The sketch plane represents 0 degrees, and counter-clockwise rotation (right-hand rule) is defined as positive.
- **axis**: The revolution axis line, defined as a 2x3 matrix. The first row specifies a pass-through base point [x, y, z], and the second row defines the directional vector [x, y, z].

---

### 3. Helix Sweep

```json
{
  "coordinate_system": {...},
  "sketch": {...},
  "constraints": {...},
  "axis": [[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
  "pitch": 5.0,
  "turns": 10.0,
  "handedness": "Right",
  "operation": "NewBody"
}

```

- **axis (Helix Axis)**: Follows the same format as the revolution axis, determining the central axis line of the helix. The helical radius is determined by the perpendicular distance from the profile sketch to this axis.
- **pitch**: The helical pitch (positive value), representing the linear distance moved along the axis direction per single full turn.
- **turns**: Total number of turns (positive value).
- **handedness**: The thread direction, which must be either "Right" or "Left".

---

### 4. Fillet

```json
{
  "near_points": [
    [10.5, 0.0, 5.0],
    [-10.5, 0.0, 5.0]
  ],
  "radius": 2.0,
  "operation": "Fillet"
}

```

- **near_points**: Boundary selection markers. A list of 3D proximity sampling points used to identify target edges in 3D space; each point resolves to a single topological edge.
- **radius**: The radius value for the fillet (positive value). It can also be formatted as a list of numbers mapped one-to-one to the entries in near_points.

---

### 5. Chamfer

```json
{
  "near_points": [
    [0.0, 10.0, 10.0]
  ],
  "plane": [1.0, 0.0, 0.0],
  "dist": 1.5,
  "angle": 30.0,
  "operation": "Chamfer"
}

```

- **near_points**: Boundary selection markers. Proximity 3D sampling points used to resolve the targeted edges for the chamfer operation.
- **plane**: The normal vector of the base plane from which the chamfer cuts into the geometry.
- **dist**: The main offset distance (positive value).
- **angle**: The cutting angle relative to the reference plane.

---

## STEP Files

Each STEP file is a standard geometry export corresponding to an executable HistCAD history. These files are provided for downstream geometry processing, visualization, and evaluations using tools that do not natively execute the parametric modeling histories.

---

## Annotation CSV Format

The structured annotation metadata provided in histcad_annotations.csv follows this column convention:

```text
uid,Modeling Process,Geometric Feature,Functional Type,NLT

```

Field descriptions:

- uid: The unique sample identifier, formatted as subset_id/sample_id.
- Modeling Process: A history-grounded text description summarizing the step-by-step CAD construction process.
- Geometric Feature: A text summary outlining the core geometric structures and topologies of the model.
- Functional Type: A short functional or semantic category label for the mechanical part.
- NLT (Natural-Language Transcription): A rich, fully structured paragraph providing a complete natural language specification of the part or assembly.

---

## Notes

- If your evaluation pipeline or benchmark requires strict one-to-one alignment across all four modalities (JSON, STEP, images, and text), please run a cross-validation script to filter by the common intersection of the uid field during data preprocessing.

---

## License

HistCAD is released under the **MIT License**.

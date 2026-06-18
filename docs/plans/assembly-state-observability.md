# Assembly State Observability Enhancement

> **Status**: Implemented (2026-06-17)
> **Files changed**: `src/freecad_mcp/tools/assembly.py`, `src/freecad_mcp/resources/freecad.py`, `src/freecad_mcp/prompts/freecad.py`, `CLAUDE.md`

## 两个互补问题

| 场景 | 问题 | 解决方案 |
| --- | --- | --- |
| 调用 `find_faces_by_constraints` 时 | Agent 需要说"法向指向+Y的面"，但不知道设计帧+Y对应什么语义 | 增加 `shape_bbox` + `bbox_side_hint`，让 agent 用几何内省推断面的位置 |
| 调用 `list_assembly_state` 后 | 拿到的 `global_orientation_quat` 是四元数，无法推断设计帧各轴当前的世界方向 | 增加 `body_frame_global`，直接展示设计帧 X/Y/Z 轴在世界坐标下的指向 |

---

## 改动一：`find_faces_by_constraints` — shape_bbox + bbox_side_hint

### 新增 `shape_bbox` 到顶层响应

```python
_result_ = {
    "object_name": obj.Name,
    "shape_bbox": {                          # ← 新增：整体包围盒
        "x_length": round(bb.XLength, 6),
        "y_length": round(bb.YLength, 6),
        "z_length": round(bb.ZLength, 6),
        "center":   arr(bb.Center),
    },
    "constraints": constraints,
    "candidates": candidates,
}
```

**作用**：agent 看到 `{z_length: 200, x_length: 50, y_length: 50}` 可直接推断"Z 轴是主轴，端面在 `+z/-z`"，无需任何坐标向量先验。

### 新增 `bbox_side_hint` 到每个候选面

为每个候选面自动检测它处于包围盒的哪一侧（可能同时属于多侧，如角落面）：

```python
def detect_bbox_sides(face_bb, shape_bb):
    eps = max(shape_bb.XLength, shape_bb.YLength, shape_bb.ZLength) * 1e-5
    sides = []
    if abs(face_bb.XMax - shape_bb.XMax) <= eps: sides.append("+x")
    if abs(face_bb.XMin - shape_bb.XMin) <= eps: sides.append("-x")
    if abs(face_bb.YMax - shape_bb.YMax) <= eps: sides.append("+y")
    if abs(face_bb.YMin - shape_bb.YMin) <= eps: sides.append("-y")
    if abs(face_bb.ZMax - shape_bb.ZMax) <= eps: sides.append("+z")
    if abs(face_bb.ZMin - shape_bb.ZMin) <= eps: sides.append("-z")
    return sides  # [] = 内部面，["+z"] = 顶端面，["+x", "+z"] = 角落面
```

在每个候选条目中追加：

```python
"bbox_side_hint": detect_bbox_sides(face.BoundBox, bb),  # e.g. ["+z"]
```

**使用模式**：agent 第一次导入零件时，空查询 `find_faces_by_constraints({})` 探索所有面，从
`bbox_side_hint` 和 `shape_bbox` 推断语义，将 `bbox_side: "+z", min_hole_count: 4` 存入 skill
上下文，后续只用符号名描述，不用浮点向量。

---

## 改动二：`list_assembly_state` / `create_connector` / `align_coordinate_systems` — body_frame_global

### 在每个 object 条目里增加 `body_frame_global`

在 `describe_object_global`（被 `create_connector` 和 `align_coordinate_systems` 共用）和
`list_assembly_state` 的 object 构建处，统一增加：

```python
rot = obj.getGlobalPlacement().Rotation
"body_frame_global": {
    "x_axis": arr(rot.multVec(FreeCAD.Vector(1, 0, 0))),  # 设计帧 +X 在世界坐标下的方向
    "y_axis": arr(rot.multVec(FreeCAD.Vector(0, 1, 0))),  # 设计帧 +Y
    "z_axis": arr(rot.multVec(FreeCAD.Vector(0, 0, 1))),  # 设计帧 +Z
}
```

这是纯计算，无副作用，无需创建任何额外的 FreeCAD 对象。

**使用模式**：零件装配旋转后，agent 调用 `list_assembly_state`，看到
`body_frame_global.z_axis: [0.3, 0, 0.95]`，知道"这个零件设计帧的+Z轴现在指向世界
[0.3, 0, 0.95] 方向"，可据此推断另一个零件应该用什么 `normal_same_direction_to` 来对齐。

---

## 改动三：`create_connector` — 修复 observation 不完整问题

当前 `create_connector` 的 observation 用内联方式只列出刚创建的那一个 connector，而
`align_coordinate_systems` 用的是 `describe_object_global`（含该零件所有 connectors）。两者不一致。

**修复方式**：在 `create_connector` 的 FreeCAD 代码片段末尾，将 observation 的 connectors 列表
改为扫描文档中所有属于该零件的 connectors：

```python
# 修复前（只含新建的那一个）
"connectors": [{"name": lcs.Name, "semantic_label": semantic_label, ...}]

# 修复后（含该零件全部 connectors，与 describe_object_global 一致）
obj_all_connectors = [
    connector_global_info(c)
    for c in doc.Objects
    if c.TypeId == "Part::LocalCoordinateSystem"
    and hasattr(c, "SemanticLabel")
    and getattr(c, "ReferenceObjectName", "") == obj.Name
]
"connectors": obj_all_connectors
```

**设计原则**：

- 每个工具的 observation = 该操作的**最小充分观测集**（受影响的零件 + 全部 connectors）
- `list_assembly_state` = agent 需要**全局视角**时显式调用（规划下一步、检查全局关系、多零件装配验证等）
- 不在工具内部自动调用 `list_assembly_state`，避免无效 token 消耗

---

## 不做的事

- **不**为每个零件创建额外的 `Part::LocalCoordinateSystem` 对象
- **不**替换 `global_orientation_quat`（保留兼容性）
- **不**修改 `find_faces_by_constraints` 的约束计算逻辑（已正确）

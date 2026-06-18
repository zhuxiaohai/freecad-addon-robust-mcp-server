# Assembly Semantic Frame

> **Status**: Implemented (2026-06-18)
> **Files changed**: `src/freecad_mcp/tools/assembly.py`, `src/freecad_mcp/resources/freecad.py`, `src/freecad_mcp/prompts/freecad.py`

## 两层坐标系架构

```text
第一层（零件级）：语义局部坐标系 LCS
  └─ 每个零件一个，定义该零件的"语义 +X/+Y/+Z 是什么意思"
  └─ 用 create_connector 建立（放在零件原点，axes=语义轴方向）
  └─ 随零件移动自动更新（AttachmentSupport + MapMode="ObjectXY"）

第二层（面级）：装配 connector LCS
  └─ 每个装配面一个，用于对齐两个零件
  └─ 依赖语义帧来描述和筛选（reference_frame_connector）
```

---

## 本 repo 实现：一处工具扩展

### `find_faces_by_constraints` 新增 `reference_frame_connector` 参数

**文件**：`src/freecad_mcp/tools/assembly.py`

**签名变化**：

```python
async def find_faces_by_constraints(
    object_name: str,
    constraints: dict[str, Any],
    doc_name: str | None = None,
    reference_frame_connector: str | None = None,  # 新增
) -> dict[str, Any]:
```

**嵌入代码变化**（替换原第 64–69 行）：

```python
# Default: use design frame (part's original modeling frame, same as current behavior)
# When reference_frame_connector is given: use that LCS connector's frame instead,
# so bbox_side/normal_* constraints are expressed in the semantic frame.
ref_connector_name = {reference_frame_connector!r}
if ref_connector_name is not None:
    ref_lcs = doc.getObject(ref_connector_name)
    if ref_lcs is None:
        raise ValueError(f"reference_frame_connector not found: {ref_connector_name!r}")
    ref_pl = ref_lcs.getGlobalPlacement()
else:
    ref_pl = obj.getGlobalPlacement()   # design frame (unchanged default)
shape = obj.Shape.copy()
shape.transformShape(ref_pl.inverse().toMatrix())
bb = shape.BoundBox
```

**效果**：不传参数时行为与当前完全一致。传入语义帧 connector 名后，`bbox_side`、`normal_same_direction_to`、`region_hint`、`bbox_side_hint` 全部在语义帧坐标下解释。

---

## agent repo 参考建议（不在本 repo 实现）

### 建议一：两阶段打标流程

```text
1. 建立语义帧
   find_faces_by_constraints（设计帧探索）→ 确认主轴方向
   create_connector(origin=[0,0,0], primary_axis=height_dir, semantic_label="body_semantic_frame")
   → 将 connector 名存入 skill 上下文

2. 建立装配 connector
   find_faces_by_constraints(reference_frame_connector="body_frame_LCS", ...)
   → 约束用语义帧坐标表达
   create_connector(origin=face_center, primary_axis=face_normal, semantic_label="base_mount")
```

### 建议二：L3 descriptor 存储格式（skill 上下文结构）

```python
{
    "part_name": "COLUMN",
    "semantic_frame_connector": "LCS001",   # 语义帧 connector 对象名
    "faces": {
        "base_mount": {
            "bbox_side": "-z",              # 语义帧坐标
            "surface_type": "Plane",
            "min_hole_count": 4,
        },
        "top_output": {
            "bbox_side": "+z",
            "surface_type": "Plane",
        },
    }
}
```

### 建议三：零件族约定（按业务场景定义）

- 立柱类：`primary_axis` = 高度方向，`bbox_side="-z"` = 安装底面
- 法兰类：`primary_axis` = 法兰面法向，`bbox_side="+z"/"-z"` = 安装面
- 轴类：`primary_axis` = 轴线方向，端面在 `±z`

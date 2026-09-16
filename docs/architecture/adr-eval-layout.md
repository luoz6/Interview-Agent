# ADR: Eval Directory Layout

- Status: Accepted
- Date: 2026-09-11
- Scope: top-level `eval/` vs target `evals/`
- Related: P0D-T02, P6

## Decision

冻结：

```text
current: eval/
target: evals/
migration phase: P6
```

当前继续保留 `eval/`，不得提前重命名或移动。到 P6 阶段按 inventory 结果迁移到 `evals/`。

## Current Reality

当前 `eval/` 只包含：

```text
eval/knowledge-v3/
    authoring/
    machine-preannotation/
```

主要内容是 knowledge-v3 的 dataset/实验产物，还不是完整的离线评估层。

## Target

目标目录：

```text
evals/
```

P6 阶段将：

- 先分类再迁目录。
- 迁移离线评估能力：
  - dataset runner
  - benchmark
  - experiment artifacts
  - offline comparison
- 保留仍影响生产决策的 runtime quality rules 在 production runtime，不迁入 `evals/`。

## Non-Goals

本 ADR：

- 不移动 `eval/`。
- 不创建 `evals/`。
- 不修改任何 evaluation script。
- 不决定每个 eval 模块的最终位置。

## Consequences

- 当前文档和工具继续使用 `eval/`。
- 到 P6 后，文档、CI、scripts、tests 中的 eval 路径需同步更新。
- 禁止直接执行 `mv eval evals`，必须先 inventory。

## Current References

当前需要继续使用：

```text
eval/knowledge-v3/machine-preannotation/*
```

P6 迁移时需一并更新相关 scripts/tests/docs。

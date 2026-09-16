#!/usr/bin/env python3
"""P0A-T03: classify direct external infrastructure imports in app layers.

This tool reuses the AST-based dependency scanner from P0A-T02 and is read-only.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scan_dependencies import KNOWN_LAYERS, REPO_ROOT, scan_app


ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"

# ``psycopg2`` must sort before ``psycopg`` so the longer prefix wins.
INFRASTRUCTURE_TARGETS: tuple[tuple[str, str], ...] = (
    ("langgraph", "workflow"),
    ("fastapi", "web"),
    ("psycopg2", "database"),
    ("psycopg", "database"),
    ("redis", "cache_queue"),
    ("celery", "cache_queue"),
    ("langchain_openai", "model_sdk"),
    ("langchain_core", "model_sdk"),
    ("langchain", "model_sdk"),
    ("openai", "model_sdk"),
    ("anthropic", "model_sdk"),
    ("google_genai", "model_sdk"),
    ("google", "model_sdk"),
    ("httpx", "provider_transport"),
    ("sentence_transformers", "model_sdk"),
)

MANDATED_TARGETS = {
    "langgraph",
    "fastapi",
    "psycopg",
    "psycopg2",
    "redis",
    "celery",
    "openai",
    "anthropic",
}


def package_for(module: str) -> str | None:
    for prefix, _ in sorted(
        INFRASTRUCTURE_TARGETS,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def category_for(package: str) -> str:
    for prefix, category in INFRASTRUCTURE_TARGETS:
        if package == prefix:
            return category
    return "unknown"


def classify(package: str, source_layer: str) -> tuple[str, str]:
    if source_layer in {"domain", "application", "ports"}:
        return (
            "明确违规",
            f"{source_layer} 层不应直接依赖基础设施包 {package}；"
            "该类依赖应由 adapter/runtime 承载。",
        )

    if source_layer == "graphs" and package == "langgraph":
        return (
            "合法技术依赖",
            "graphs 层当前承载 LangGraph workflow wiring，直接依赖 langgraph 符合其当前职责。",
        )

    if source_layer == "api" and package == "fastapi":
        return (
            "合法技术依赖",
            "api 层是 FastAPI HTTP 边界，直接依赖 fastapi 属于边界实现。",
        )

    if source_layer == "other" and package == "fastapi":
        return (
            "合法技术依赖",
            "app/main.py 是 API 启动/组合入口，使用 fastapi 属于边界实现。",
        )

    if source_layer == "adapters" and package in {
        "psycopg",
        "psycopg2",
        "redis",
        "celery",
        "openai",
        "httpx",
        "langgraph",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "google",
        "google_genai",
        "sentence_transformers",
    }:
        return (
            "合法技术依赖",
            f"adapters 层负责具体技术适配，直接依赖 {package} 属于 adapter 职责。",
        )

    if source_layer == "runtime" and package in {
        "psycopg",
        "psycopg2",
        "redis",
        "celery",
        "fastapi",
        "langgraph",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "openai",
        "anthropic",
        "httpx",
        "google",
        "google_genai",
        "sentence_transformers",
    }:
        return (
            "合法技术依赖",
            "runtime 是 composition root，可合法选择并构造具体基础设施实现。",
        )

    if source_layer == "evals":
        return (
            "合法技术依赖",
            "evals 是离线评测与诊断边界，可调用受控 provider、transport 与数据库探针。",
        )

    if source_layer == "services":
        return (
            "潜在违规",
            f"services 仍直接依赖 {package}，说明基础设施/工作流/provider 职责尚未迁入 "
            "adapter、graphs 或 runtime；需由 P0A-T04/P2 进一步确认。",
        )

    if source_layer == "agents":
        return (
            "潜在违规",
            f"agents 层直接依赖 {package} 可能绕过 ports/services 边界；"
            "需结合调用链进一步确认。",
        )

    return (
        "暂时无法判断",
        f"{source_layer} 依赖 {package} 的合法性需要进一步人工判断，"
        "不因包名自动判定违规。",
    )


def _layer_classification_matrix(
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    classifications = (
        "合法技术依赖",
        "潜在违规",
        "明确违规",
        "暂时无法判断",
    )
    grouped: dict[tuple[str, str], int] = {
        (layer, classification): 0
        for layer in KNOWN_LAYERS
        for classification in classifications
    }
    for match in matches:
        grouped[(match["source_layer"], match["classification"])] += 1
    return [
        {"source_layer": source_layer, "classification": classification, "edges": count}
        for (source_layer, classification), count in sorted(grouped.items())
    ]


def _package_summary(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for match in matches:
        package = match["package"]
        if package not in grouped:
            grouped[package] = {
                "package": package,
                "category": category_for(package),
                "mandated": package in MANDATED_TARGETS,
                "edges": 0,
                "source_files": set(),
                "source_layers": set(),
            }
        grouped[package]["edges"] += 1
        grouped[package]["source_files"].add(match["source_file"])
        grouped[package]["source_layers"].add(match["source_layer"])

    result = []
    for package, data in sorted(grouped.items()):
        result.append(
            {
                "package": package,
                "category": data["category"],
                "mandated": data["mandated"],
                "edges": int(data["edges"]),
                "source_files": len(data["source_files"]),
                "source_layers": sorted(data["source_layers"]),
            }
        )

    present = {item["package"] for item in result}
    for package, category in INFRASTRUCTURE_TARGETS:
        if package not in present:
            result.append(
                {
                    "package": package,
                    "category": category,
                    "mandated": package in MANDATED_TARGETS,
                    "edges": 0,
                    "source_files": 0,
                    "source_layers": [],
                }
            )
    return sorted(result, key=lambda item: item["package"])


def _top_files(matches: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter(match["source_file"] for match in matches)
    return [
        {"source_file": source_file, "edges": count}
        for source_file, count in counter.most_common(limit)
    ]


def build_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Infrastructure Import Scan")
    lines.append("")
    lines.append("## Scope and Method")
    lines.append("")
    lines.append(
        "- 输入：P0A-T02 的 AST dependency scan 中 `target_layer == external` 的 raw edges。"
    )
    lines.append(
        "- 目标：mandated infrastructure packages、model SDK 与 provider transport。"
    )
    lines.append(
        "- 规则：不因包名自动判定违规；classification 同时考虑 package 与 source layer。"
    )
    lines.append("")
    lines.append("## Package Summary")
    lines.append("")
    lines.append("| package | category | mandated | edges | source_files | source_layers |")
    lines.append("| --- | --- | --- | ---: | ---: | --- |")
    for row in result["summary_by_package"]:
        lines.append(
            "| "
            f"{row['package']} | {row['category']} | "
            f"{'yes' if row['mandated'] else 'no'} | {row['edges']} | "
            f"{row['source_files']} | {', '.join(row['source_layers']) or '-'} |"
        )
    lines.append("")
    lines.append("## Layer x Classification Matrix")
    lines.append("")
    lines.append("| source_layer | classification | edges |")
    lines.append("| --- | --- | ---: |")
    for row in result["layer_classification_matrix"]:
        lines.append(
            f"| {row['source_layer']} | {row['classification']} | {row['edges']} |"
        )
    lines.append("")
    lines.append("## Top Source Files")
    lines.append("")
    lines.append("| source_file | edges |")
    lines.append("| --- | ---: |")
    for row in result["top_source_files"]:
        lines.append(f"| {row['source_file']} | {row['edges']} |")
    lines.append("")
    lines.append("## Classification Counts")
    lines.append("")
    for classification, count in result["classification_counts"].items():
        lines.append(f"- {classification}: {count}")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- 动态 import 不纳入本报告；P0A-T02 已单独记录动态 import limitation。"
    )
    lines.append(
        "- 本次只分类强制清单和 provider/model SDK/transport；"
        "pydantic 等通用框架不属于强制清单，未做违规分类。"
    )
    lines.append(
        "- `services` 中大量直接基础设施依赖当前标记为“潜在违规”而非“明确违规”，"
        "因为 services 仍是 brownfield 生产实现；最终去向需 P0A-T04/P2 确认。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    scan_result = scan_app()
    matches: list[dict[str, Any]] = []

    for edge in scan_result["external_edges"]:
        package = package_for(edge["target_module"])
        if package is None:
            continue
        classification, reason = classify(package, edge["source_layer"])
        matches.append(
            {
                "source_file": edge["source_file"],
                "source_module": edge["source_module"],
                "source_layer": edge["source_layer"],
                "target_module": edge["target_module"],
                "import_type": edge["import_type"],
                "line": edge["line"],
                "package": package,
                "category": category_for(package),
                "classification": classification,
                "reason": reason,
            }
        )

    raw_classification_counts = Counter(
        match["classification"] for match in matches
    )
    classification_counts = {
        classification: raw_classification_counts.get(classification, 0)
        for classification in (
            "合法技术依赖",
            "潜在违规",
            "明确违规",
            "暂时无法判断",
        )
    }
    result = {
        "metadata": {
            "repo_root": str(REPO_ROOT.resolve()),
            "files_scanned": scan_result["metadata"]["files_scanned"],
            "external_edge_count": scan_result["metadata"]["external_edge_count"],
            "infrastructure_match_count": len(matches),
            "mandated_targets": sorted(MANDATED_TARGETS),
        },
        "matches": matches,
        "summary_by_package": _package_summary(matches),
        "layer_classification_matrix": _layer_classification_matrix(matches),
        "top_source_files": _top_files(matches),
        "classification_counts": classification_counts,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = ARTIFACTS_DIR / "infrastructure-imports.json"
    md_path = ARTIFACTS_DIR / "infrastructure-imports.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(build_markdown(result), encoding="utf-8")

    print(f"wrote {json_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"wrote {md_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"infrastructure_match_count={len(matches)}")
    for classification, count in sorted(classification_counts.items()):
        print(f"{classification}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

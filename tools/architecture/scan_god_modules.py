#!/usr/bin/env python3
"""P0A-T05: God-module baseline from static LOC/import facts.

Read-only. It uses the P0A-T02 dependency-edges.json and does not import
application modules.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
DEPENDENCY_EDGES_PATH = ARTIFACTS_DIR / "dependency-edges.json"

REQUIRED_MODULES = {
    "app/graphs/durable_interview_graph.py",
    "app/services/runtime.py",
    "app/services/llm.py",
}


def _load_edges() -> dict[str, Any]:
    return json.loads(DEPENDENCY_EDGES_PATH.read_text(encoding="utf-8"))


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8-sig").splitlines())
    except Exception:
        return 0


def _external_technologies(external_edges: list[dict[str, Any]], source_file: str) -> list[str]:
    packages: Counter[str] = Counter()
    for edge in external_edges:
        if edge.get("source_file") != source_file:
            continue
        target = edge.get("target_module", "")
        packages[target.split(".", 1)[0]] += 1
    return [f"{pkg}:{count}" for pkg, count in packages.most_common(8)]


def _responsibility_groups(module: str) -> str:
    if "durable_interview_graph" in module:
        return (
            "business rule; application orchestration; context; evidence; "
            "provider interaction; durable execution; LangGraph wiring"
        )
    if module.endswith("services.runtime"):
        return "composition root; lifecycle; dependency wiring; runtime resources"
    if module.endswith("services.llm"):
        return "provider config; LLM protocol; OpenAI adapter; prompt/output shaping"
    if "postgres" in module:
        return "persistence; SQL; transaction; schema"
    if "workflow" in module or "graph" in module:
        return "workflow orchestration; durable execution; state transitions"
    if "report" in module:
        return "report generation; quality; persistence; worker"
    if "context" in module:
        return "context selection; compression; identity; budget"
    if "principal_memory" in module:
        return "principal memory; consent; ledger; retrieval"
    return "mixed application/domain logic"


def _business_concepts(module: str) -> str:
    if "durable_interview_graph" in module:
        return "interview session, follow-up decision, generation, question state, report handoff"
    if module.endswith("services.runtime"):
        return "runtime dependency construction, stores, workflows, LLM, lifecycle"
    if module.endswith("services.llm"):
        return "interview/report LLM provider, plan/report output modes, prompts"
    return module.split("/")[-1].replace(".py", "").replace("_", " ")


def _candidate_extraction_boundaries(module: str) -> str:
    if "durable_interview_graph" in module:
        return (
            "extract pure follow-up/decision/context/evidence rules first; "
            "keep LangGraph node/edge wiring in workflow adapter"
        )
    if module.endswith("services.runtime"):
        return (
            "split concrete repository/provider/workflow construction into "
            "runtime modules and persistence adapters"
        )
    if module.endswith("services.llm"):
        return (
            "separate provider interface from OpenAI/transport adapter; "
            "keep prompt versions explicitly governed"
        )
    return "needs responsibility-map review before choosing split lines"


def _module_path_for_file(relative_file: str) -> Path:
    return REPO_ROOT / relative_file


def main() -> int:
    data = _load_edges()
    internal_edges = data.get("edges", [])
    external_edges = data.get("external_edges", [])

    incoming: dict[str, set[str]] = defaultdict(set)
    internal_targets: dict[str, set[str]] = defaultdict(set)
    for edge in internal_edges:
        target = edge.get("target_module", "")
        source_file = edge.get("source_file", "")
        incoming[target].add(source_file)
        internal_targets[source_file].add(target)

    raw_import_edges: Counter[str] = Counter()
    for edge in (*internal_edges, *external_edges):
        raw_import_edges[edge.get("source_file", "")] += 1

    rows: list[dict[str, Any]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative_file = path.relative_to(REPO_ROOT).as_posix()
        module_name = (
            relative_file[:-3].replace("/", ".").replace("\\", ".")
            if relative_file.endswith(".py")
            else relative_file.replace("/", ".").replace("\\", ".")
        )
        if path.name == "__init__.py":
            module_name = ".".join(module_name.split(".")[:-1])
        loc = _line_count(path)
        fan_in = len(incoming.get(module_name, set()))
        fan_out = len(internal_targets.get(relative_file, set()))
        import_count = raw_import_edges.get(relative_file, 0)
        god_score = loc + fan_in * 8 + fan_out * 4
        rows.append(
            {
                "file": relative_file,
                "module": module_name,
                "loc": loc,
                "import_count": import_count,
                "fan_in": fan_in,
                "fan_out": fan_out,
                "god_score": god_score,
                "external_technologies": _external_technologies(
                    external_edges, relative_file
                ),
                "responsibility_groups": _responsibility_groups(module_name),
                "business_concepts": _business_concepts(module_name),
                "candidate_extraction_boundaries": _candidate_extraction_boundaries(
                    module_name
                ),
            }
        )

    top_by_score = sorted(rows, key=lambda r: r["god_score"], reverse=True)
    selected: dict[str, dict[str, Any]] = {}
    for row in top_by_score:
        if row["file"] in REQUIRED_MODULES or len(selected) < 25:
            selected[row["file"]] = row

    lines: list[str] = []
    lines.append("# God Module Baseline")
    lines.append("")
    lines.append("## Definitions")
    lines.append("")
    lines.append(
        "- `loc`: physical line count, including blanks/comments/imports; "
        "observation metric only."
    )
    lines.append(
        "- `import_count`: raw AST import edges originating from the file "
        "(internal + external)."
    )
    lines.append(
        "- `fan_in`: unique internal `app.*` source files importing this module."
    )
    lines.append(
        "- `fan_out`: unique internal `app.*` target modules imported by this file."
    )
    lines.append(
        "- `god_score`: heuristic `loc + fan_in*8 + fan_out*4`; used only for ranking."
    )
    lines.append("")
    lines.append("## Top Candidate Modules")
    lines.append("")
    lines.append(
        "| module | loc | import_count | fan_in | fan_out | external_technologies |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for row in sorted(
        selected.values(),
        key=lambda r: (-r["god_score"], r["module"]),
    ):
        tech = ", ".join(row["external_technologies"][:5]) or "-"
        lines.append(
            f"| {row['module']} | {row['loc']} | {row['import_count']} | "
            f"{row['fan_in']} | {row['fan_out']} | {tech} |"
        )
    lines.append("")
    lines.append("## Candidate Responsibility and Extraction Signals")
    lines.append("")
    lines.append(
        "| module | responsibility_groups | business_concepts | candidate_extraction_boundaries |"
    )
    lines.append("| --- | --- | --- | --- |")
    for row in sorted(selected.values(), key=lambda r: (-r["god_score"], r["module"])):
        lines.append(
            f"| {row['module']} | {row['responsibility_groups']} | "
            f"{row['business_concepts']} | {row['candidate_extraction_boundaries']} |"
        )
    lines.append("")
    lines.append("## Required Module Details")
    lines.append("")
    for file in sorted(REQUIRED_MODULES):
        row = selected[file]
        lines.append(f"### {row['module']}")
        lines.append("")
        lines.append(f"- loc: {row['loc']}")
        lines.append(f"- import_count: {row['import_count']}")
        lines.append(f"- fan_in: {row['fan_in']}")
        lines.append(f"- fan_out: {row['fan_out']}")
        lines.append(
            f"- external_technologies: {', '.join(row['external_technologies']) or '-'}"
        )
        lines.append(f"- responsibility_groups: {row['responsibility_groups']}")
        lines.append(f"- business_concepts: {row['business_concepts']}")
        lines.append(
            f"- candidate_extraction_boundaries: {row['candidate_extraction_boundaries']}"
        )
        lines.append("")

    lines.append("## durable_interview_graph.py Responsibility Breakdown")
    lines.append("")
    lines.append(
        "This breakdown is required by P0A-T05 and is observational only; "
        "no graph split is performed here."
    )
    lines.append("")
    lines.append(
        "| responsibility | representative functions/classes | notes |"
    )
    lines.append("| --- | --- | --- |")
    lines.append(
        "| business rule | `_followup_guard_updates`, `_is_duplicate_followup_text`, "
        "`apply_skip`, `apply_finish` | follow-up limits, duplicate detection, "
        "question progression rules |"
    )
    lines.append(
        "| application orchestration | `prepare_or_load_decision`, "
        "`prepare_generation`, `prepare_main_question`, `commit_*` | coordinates "
        "stores/services and state transitions |"
    )
    lines.append(
        "| context | `_build_examiner_context*`, `_main_question_context_projection`, "
        "`_recent_conversation_*` | builds provider context, selection and budget "
        "projections |"
    )
    lines.append(
        "| evidence | `resolve_evidence_by_ids`, `parse_question_knowledge_binding`, "
        "`_interview_owner_scope` | evidence binding/owner-scope behavior |"
    )
    lines.append(
        "| provider interaction | `generate_followup`, "
        "`generate_main_question_node`, `_invoke_main_question_with_timeout` | "
        "invokes LLM providers and validates output |"
    )
    lines.append(
        "| durable execution | `GenerationLeaseHeartbeat`, `validate_command`, "
        "`enqueue_retry`, `wait_for_retry`, generation store calls | lease/heartbeat, "
        "command versioning and retry semantics |"
    )
    lines.append(
        "| LangGraph wiring | `build_durable_interview_graph*`, `route_*`, "
        "`wait_for_answer`, `project_state_node` | StateGraph nodes, conditional "
        "edges and schema binding |"
    )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- Fan-in/fan-out are static `app.*` import facts; tests/scripts/Celery "
        "task names and dynamic imports are excluded."
    )
    lines.append(
        "- `god_score` is an observation ranking, not an architecture verdict."
    )
    lines.append(
        "- No graph or production code was changed."
    )
    lines.append("")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACTS_DIR / "god-module-baseline.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"candidate_count={len(selected)}")
    for file in sorted(REQUIRED_MODULES):
        row = selected[file]
        print(
            f"{file}: loc={row['loc']} fan_in={row['fan_in']} "
            f"fan_out={row['fan_out']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

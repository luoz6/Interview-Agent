from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

from app.services.knowledge_business_eval import (
    BusinessEvalCase,
    BusinessEvalEngineIdentity,
    BusinessEvalGovernance,
    BusinessEvalOutput,
    KnowledgeBusinessEvalDataset,
    build_blind_business_eval_package,
)
from app.services.knowledge_eval_artifacts_v3 import canonical_sha256
from app.services.llm import OpenAIInterviewLLM


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "eval/knowledge-v3/machine-preannotation/dataset.json"
DEFAULT_BASELINE = (
    ROOT / "eval/knowledge-v3/machine-preannotation/legacy-tuning-diagnostic.json"
)
DEFAULT_CANDIDATE = (
    ROOT
    / "eval/knowledge-v3/machine-preannotation/hybrid-rrf-tuning-candidate.json"
)
DEFAULT_CORPUS = ROOT / "app/data/knowledge_v2"
SCENARIOS = (
    "strong_answer",
    "partial_answer",
    "typical_error",
    "misunderstood_question",
    "skipped_or_empty",
    "terminology_stacking",
    "factual_hallucination",
    "cross_domain_answer",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build real Legacy/Hybrid Follow-up and Reviewer outputs, then freeze "
            "randomized blind A/B packages without fabricating human annotations."
        )
    )
    parser.add_argument("--retrieval-dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--case-families", type=int, default=25, choices=(25, 30, 40, 50))
    parser.add_argument(
        "--resume-cache",
        type=Path,
        help="Private resumable Provider-output cache. Defaults inside output-dir.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = (args.resume_cache or output_dir / "provider-output-cache.json").resolve()
    llm = OpenAIInterviewLLM()

    def invoke(prompt: str) -> dict[str, Any]:
        model = llm.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=700)
        response = model.invoke(prompt)
        content = str(getattr(response, "content", response)).strip()
        return _parse_json_object(content)

    dataset, receipt = build_business_dataset(
        retrieval_dataset_path=args.retrieval_dataset,
        baseline_path=args.baseline,
        candidate_path=args.candidate,
        corpus_dir=args.corpus_dir,
        case_families=args.case_families,
        invoke=invoke,
        cache_path=cache_path,
    )
    dataset.validate_release_shape()
    created_at = datetime.now(timezone.utc)
    tuning_package, tuning_mapping = build_blind_business_eval_package(
        dataset,
        split="tuning",
        seed=args.seed,
        created_at=created_at,
    )
    holdout_package, holdout_mapping = build_blind_business_eval_package(
        dataset,
        split="holdout",
        seed=args.seed,
        created_at=created_at,
    )

    artifacts = {
        "source-dataset.json": dataset.model_dump(mode="json"),
        "tuning-blind-package.json": tuning_package.model_dump(mode="json"),
        "tuning-unblinding-key.json": tuning_mapping.model_dump(mode="json"),
        "tuning-annotations-template.json": _annotation_template(tuning_package),
        "holdout-blind-package.json": holdout_package.model_dump(mode="json"),
        "holdout-unblinding-key.json": holdout_mapping.model_dump(mode="json"),
        "holdout-annotations-template.json": _annotation_template(holdout_package),
    }
    for name, payload in artifacts.items():
        _write_new_json(output_dir / name, payload)

    receipt = {
        **receipt,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "dataset_sha256": dataset.dataset_sha256(),
        "case_count": len(dataset.cases),
        "tuning_case_count": len(tuning_package.cases),
        "holdout_case_count": len(holdout_package.cases),
        "tuning_package_sha256": tuning_package.package_sha256,
        "holdout_package_sha256": holdout_package.package_sha256,
        "human_annotation_status": "pending",
        "independent_human_annotations_present": False,
        "eligible_as_release_evidence": False,
        "next_required_action": (
            "two independent qualified human annotators must score every blind "
            "option before consensus, comparison, or a release decision"
        ),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    _write_new_json(output_dir / "generation-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def build_business_dataset(
    *,
    retrieval_dataset_path: Path,
    baseline_path: Path,
    candidate_path: Path,
    corpus_dir: Path,
    case_families: int,
    invoke: Callable[[str], dict[str, Any]],
    cache_path: Path,
) -> tuple[KnowledgeBusinessEvalDataset, dict[str, Any]]:
    retrieval_dataset = _read_json(retrieval_dataset_path)
    baseline = _read_json(baseline_path)
    candidate = _read_json(candidate_path)
    if baseline["dataset_sha256"] != candidate["dataset_sha256"]:
        raise ValueError("Legacy and Hybrid artifacts use different datasets")
    if baseline["split"] != "tuning" or candidate["split"] != "tuning":
        raise ValueError("business source generation is limited to tuning retrieval artifacts")
    source_by_id = {case["case_id"]: case for case in retrieval_dataset["cases"]}
    baseline_by_id = {case["case_id"]: case for case in baseline["cases"]}
    candidate_by_id = {case["case_id"]: case for case in candidate["cases"]}
    ordered_ids = [
        case["case_id"]
        for case in retrieval_dataset["cases"]
        if case["split"] == "tuning"
        and case["case_id"] in baseline_by_id
        and case["case_id"] in candidate_by_id
    ][:case_families]
    if len(ordered_ids) != case_families:
        raise ValueError("insufficient paired tuning cases for business evaluation")

    cache = _load_cache(cache_path)
    code_revision = _git_output("rev-parse", "HEAD")
    code_tree_sha256 = canonical_sha256(
        {
            "head": code_revision,
            "status": _git_output("status", "--porcelain=v1", "--untracked-files=no"),
        }
    )
    baseline_identity = _business_identity("legacy", baseline["identity"])
    candidate_identity = _business_identity("hybrid-weighted-rrf", candidate["identity"])
    cases: list[BusinessEvalCase] = []
    provider_calls = 0
    cache_hits = 0
    tuning_family_count = round(case_families * 0.75)

    for index, case_id in enumerate(ordered_ids):
        source = source_by_id[case_id]
        baseline_case = baseline_by_id[case_id]
        candidate_case = candidate_by_id[case_id]
        scenario = SCENARIOS[index % len(SCENARIOS)]
        split = "tuning" if index < tuning_family_count else "holdout"
        question = _repair_legacy_mojibake(source["query_text"])
        primary_id = (source.get("primary_relevant_chunk_ids") or [""])[0]
        primary_content = _load_chunk_content(corpus_dir, primary_id)
        answer = _synthetic_answer(scenario, primary_content, primary_id)
        engine_outputs: dict[str, dict[str, Any]] = {}
        for engine_id, retrieval_case in (
            ("legacy", baseline_case),
            ("hybrid-weighted-rrf", candidate_case),
        ):
            evidence_ids = tuple(retrieval_case.get("selected_evidence_ids", ()))
            cache_key = canonical_sha256(
                {
                    "schema": "knowledge-business-provider-bundle-v1",
                    "model": "configured-runtime-model",
                    "engine_id": engine_id,
                    "question": question,
                    "answer": answer,
                    "evidence_ids": evidence_ids,
                }
            )
            if cache_key in cache:
                engine_outputs[engine_id] = cache[cache_key]
                cache_hits += 1
                continue
            prompt = _provider_prompt(
                engine_id=engine_id,
                question=question,
                answer=answer,
                evidence_ids=evidence_ids,
                corpus_dir=corpus_dir,
            )
            payload = _validate_provider_bundle(invoke(prompt))
            cache[cache_key] = payload
            _write_cache(cache_path, cache)
            engine_outputs[engine_id] = payload
            provider_calls += 1

        combined_evidence = tuple(
            dict.fromkeys(
                (*baseline_case.get("selected_evidence_ids", ()),
                 *candidate_case.get("selected_evidence_ids", ()))
            )
        )
        for target in ("followup", "reviewer"):
            unavailable = target == "reviewer" and index == case_families - 1
            insufficient = target == "reviewer" and index == case_families - 2
            system_failure = unavailable
            baseline_output = _business_output(
                engine_outputs["legacy"],
                target=target,
                evidence_ids=tuple(baseline_case.get("selected_evidence_ids", ())),
                unavailable=unavailable,
                insufficient=insufficient,
                system_failure=system_failure,
            )
            candidate_output = _business_output(
                engine_outputs["hybrid-weighted-rrf"],
                target=target,
                evidence_ids=tuple(candidate_case.get("selected_evidence_ids", ())),
                unavailable=unavailable,
                insufficient=insufficient,
                system_failure=system_failure,
            )
            cases.append(
                BusinessEvalCase(
                    case_id=f"business-{index + 1:03d}-{target}",
                    case_family=f"business-family-{index + 1:03d}",
                    split=split,
                    target=target,
                    scenario_type=scenario,
                    role="backend engineer",
                    seniority="senior",
                    question=question,
                    candidate_answer=answer,
                    evidence_ids=() if unavailable else combined_evidence,
                    evidence_availability="unavailable" if unavailable else "available",
                    evidence_sufficiency=(
                        "not_evaluated" if unavailable else "insufficient" if insufficient else "sufficient"
                    ),
                    system_failure_scenario=system_failure,
                    baseline_output=baseline_output,
                    candidate_output=candidate_output,
                )
            )

    frozen_at = datetime.now(timezone.utc)
    dataset = KnowledgeBusinessEvalDataset(
        dataset_version="knowledge-business-rmqv4-2026-08-13-v1",
        baseline_identity=baseline_identity,
        candidate_identity=candidate_identity,
        governance=BusinessEvalGovernance(
            protocol_version="knowledge-business-blind-protocol-v1",
            split_frozen=True,
            outputs_frozen=True,
            randomized_blind_ab=True,
            minimum_annotators_per_case=2,
            annotator_roles=("independent qualified senior technical interviewer",),
            minimum_qualification=(
                "at least five years of backend engineering and structured interviewing"
            ),
            adjudication_rule="a third qualified reviewer adjudicates every disagreement",
            agreement_metric="krippendorff_alpha",
            minimum_agreement=0.8,
            frozen_at=frozen_at,
            provenance_record_sha256=canonical_sha256(
                {
                    "retrieval_dataset_sha256": baseline["dataset_sha256"],
                    "baseline_artifact_sha256": baseline["artifact_sha256"],
                    "candidate_artifact_sha256": candidate["artifact_sha256"],
                    "builder_code_revision": code_revision,
                    "builder_code_tree_sha256": code_tree_sha256,
                }
            ),
        ),
        cases=tuple(cases),
    )
    return dataset, {
        "schema_version": "knowledge-business-generation-receipt-v1",
        "retrieval_dataset_sha256": baseline["dataset_sha256"],
        "baseline_artifact_sha256": baseline["artifact_sha256"],
        "candidate_artifact_sha256": candidate["artifact_sha256"],
        "builder_code_revision": code_revision,
        "builder_code_tree_sha256": code_tree_sha256,
        "provider_bundle_calls": provider_calls,
        "provider_cache_hits": cache_hits,
    }


def _business_identity(engine_id: str, retrieval_identity: dict) -> BusinessEvalEngineIdentity:
    return BusinessEvalEngineIdentity(
        engine_id=engine_id,
        engine_version=retrieval_identity["engine_version"],
        code_revision=retrieval_identity["code_revision"],
        code_tree_sha256=retrieval_identity["code_tree_sha256"],
        profile_sha256=retrieval_identity["profile_sha256"],
    )


def _business_output(
    bundle: dict[str, Any],
    *,
    target: str,
    evidence_ids: tuple[str, ...],
    unavailable: bool,
    insufficient: bool,
    system_failure: bool,
) -> BusinessEvalOutput:
    if target == "followup":
        return BusinessEvalOutput(text=bundle["followup"], evidence_ids=evidence_ids)
    if system_failure:
        return BusinessEvalOutput(
            text="评审依赖不可用，本次不输出候选人分数或技术判断。",
            score=None,
            repeated_scores=(0.0, 0.0),
            confidence="not_scorable",
            evidence_ids=(),
            system_failure=True,
        )
    if insufficient:
        return BusinessEvalOutput(
            text="当前证据不足以形成稳定评分；仅记录缺失项，不作能力结论。",
            score=None,
            repeated_scores=(0.0, 0.0),
            confidence="not_scorable",
            evidence_ids=evidence_ids,
        )
    runs = bundle["reviewer_runs"]
    scores = tuple(float(run["score"]) for run in runs)
    confidence = min((run["confidence"] for run in runs), key=_confidence_order)
    return BusinessEvalOutput(
        text="\n\n".join(run["text"] for run in runs),
        score=round(fmean(scores), 3),
        repeated_scores=scores,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )


def _confidence_order(value: str) -> int:
    return {"not_scorable": 0, "low": 1, "medium": 2, "high": 3}[value]


def _provider_prompt(
    *,
    engine_id: str,
    question: str,
    answer: str,
    evidence_ids: tuple[str, ...],
    corpus_dir: Path,
) -> str:
    evidence = [
        {"evidence_id": evidence_id, "content": _load_chunk_content(corpus_dir, evidence_id)[:2400]}
        for evidence_id in evidence_ids[:5]
    ]
    shape = {
        "followup": "one concise Simplified Chinese follow-up question",
        "reviewer_runs": [
            {"score": 0, "confidence": "high|medium|low|not_scorable", "text": "review"},
            {"score": 0, "confidence": "high|medium|low|not_scorable", "text": "independent second review"},
        ],
    }
    return (
        "You are producing frozen outputs for a blind technical-interview A/B evaluation.\n"
        "Return JSON only and exactly match the requested shape.\n"
        "The follow-up must target the single highest-value missing or incorrect signal in the candidate answer, be concise, avoid repetition, and never reveal an ideal answer.\n"
        "Perform two independent Reviewer passes. Each pass scores 0-100 using only the candidate answer and supplied evidence, states calibrated confidence, identifies support and missing evidence, and never rewards terminology without mechanism.\n"
        "Treat retrieved knowledge as a rubric, never as something the candidate said. Do not invent facts or evidence IDs.\n"
        f"engine_id={engine_id}\n"
        f"OUTPUT_SHAPE={json.dumps(shape, ensure_ascii=False)}\n"
        f"QUESTION={question}\n"
        f"CANDIDATE_ANSWER={answer or '[EMPTY]'}\n"
        f"RETRIEVED_EVIDENCE={json.dumps(evidence, ensure_ascii=False)}"
    )


def _validate_provider_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"followup", "reviewer_runs"}:
        raise ValueError("Provider bundle must contain exactly followup and reviewer_runs")
    followup = str(payload["followup"]).strip()
    runs = payload["reviewer_runs"]
    if not followup or not isinstance(runs, list) or len(runs) != 2:
        raise ValueError("Provider bundle requires one followup and two reviewer runs")
    normalized_runs = []
    for run in runs:
        score = float(run["score"])
        confidence = str(run["confidence"])
        text = str(run["text"]).strip()
        if not 0 <= score <= 100 or confidence not in {"high", "medium", "low", "not_scorable"} or not text:
            raise ValueError("invalid Reviewer run")
        normalized_runs.append({"score": score, "confidence": confidence, "text": text})
    return {"followup": followup, "reviewer_runs": normalized_runs}


def _synthetic_answer(scenario: str, content: str, chunk_id: str) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？])", content) if item.strip()]
    core = "".join(sentences[:2]) or f"我会围绕 {chunk_id} 分析机制、故障边界和验证指标。"
    if scenario == "strong_answer":
        return core
    if scenario == "partial_answer":
        return f"我会采用 {chunk_id} 对应的常见方案，但暂时没有补充失败边界和验证指标。"
    if scenario == "typical_error":
        return f"只要启用 {chunk_id} 并增加重试次数，就可以彻底避免这类问题。"
    if scenario == "misunderstood_question":
        return "我主要会优化前端渲染性能，包括组件拆分、样式缓存和首屏资源加载。"
    if scenario == "skipped_or_empty":
        return ""
    if scenario == "terminology_stacking":
        return f"这个方案会用到 {chunk_id}、高可用、幂等、熔断、限流、最终一致性和可观测性。"
    if scenario == "factual_hallucination":
        return f"{chunk_id} 能提供跨节点严格串行化，所以任何故障下都不会重复执行或丢失状态。"
    if scenario == "cross_domain_answer":
        return "我会通过 CSS Grid、浏览器合成层和虚拟 DOM diff 来解决数据库连接与消息投递问题。"
    raise ValueError(f"unsupported scenario: {scenario}")


def _load_chunk_content(corpus_dir: Path, chunk_id: str) -> str:
    manifest = _read_json(corpus_dir / "manifest.json")
    item = next((row for row in manifest["chunks"] if row["chunk_id"] == chunk_id), None)
    if item is None:
        return ""
    text = (corpus_dir / item["source_path"]).read_text(encoding="utf-8")
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL).strip()


def _parse_json_object(content: str) -> dict[str, Any]:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Provider response did not contain JSON")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Provider JSON root must be an object")
    return payload


def _repair_legacy_mojibake(value: str) -> str:
    """Repair the historical GBK-bytes-as-Latin-1 Eval V3 query encoding.

    The retrieval dataset is intentionally left immutable because its frozen
    SHA and runtime artifacts bind the original text. Human-facing business
    packages must contain readable questions, so normalization happens only at
    this derivative boundary and fails closed when a suspicious value cannot
    be repaired.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("business evaluation question must not be empty")
    suspicious = sum(character in "ÔÚºó¶ËÃæÊÔÖÐÈçÎªµÄÁË£¬¡°¡±" for character in text)
    if suspicious < 2:
        return text
    try:
        repaired = text.encode("latin-1").decode("gbk").strip()
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise ValueError("legacy Eval V3 question mojibake could not be repaired") from exc
    if not any("\u4e00" <= character <= "\u9fff" for character in repaired):
        raise ValueError("repaired business evaluation question is not readable Chinese")
    return repaired


def _annotation_template(package) -> dict[str, Any]:
    return {
        "schema_version": "knowledge-business-annotations-v1",
        "dataset_sha256": package.dataset_sha256,
        "package_sha256": package.package_sha256,
        "split": package.split,
        "governance": None,
        "records": [],
        "consensus": [],
        "instructions": {
            "human_annotations_required": True,
            "minimum_independent_annotators_per_case": 2,
            "do_not_use_engine_labels": True,
            "do_not_fill_with_model_generated_ratings": True,
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cache(path: Path) -> dict[str, Any]:
    return _read_json(path) if path.exists() else {}


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloadFile, getJson } from "../api/client";
import { ReportDetailPage } from "./ReportDetailPage";

vi.mock("../api/client", () => ({
  downloadFile: vi.fn(),
  getJson: vi.fn(),
}));

const feedback = (questionId, questionText, score) => ({
  question_id: questionId,
  question_text: questionText,
  user_answer: "候选人回答",
  answer_state: "answered",
  score,
  evaluation_status: "evaluated",
  applicable_dimensions: ["depth"],
  dimension_evidence: [],
  highlights: ["回答有清楚边界"],
  rationale: "回答包含可验证的技术依据。",
  critique: "还可以补充权衡。",
  better_answer: "先说明结论，再用候选人已经陈述的事实解释权衡。",
  references: [],
});

const reportResponse = {
  active_artifact: {
    report_id: "report-2",
    session_id: "session-1",
    revision: 2,
    created_at: "2026-08-06T07:30:00Z",
    active: true,
    schema_version: "report-artifact-v2",
    report_schema_version: "report-schema-v2",
    scoring_rubric_version: "rubric-v1",
    generation_status: "complete",
    generation_reason_code: "normal",
    score_status: "partial",
    score_reason_code: "partial_coverage",
    coverage_status: "partial",
    report_path: "microbatch",
    overall_score: 81,
    overall_dimension_scores: {
      breadth: null,
      depth: 81,
      architecture: null,
      engineering: 80,
      communication: 82,
    },
    evaluated_count: 2,
    total_eligible_count: 3,
    evidence_count: 2,
    dimension_evaluations: {
      breadth: { status: "insufficient_evidence" },
      depth: { status: "evaluated" },
      architecture: { status: "not_evaluated" },
      engineering: { status: "evaluated" },
      communication: { status: "evaluated" },
    },
    source_job_id: "job-success",
    payload: {
      summary: "本轮回答技术边界清楚，但覆盖尚不完整。",
      highlights: ["能够主动说明技术边界"],
      strengths: [{
        claim_id: "strength-1",
        text: "能够主动说明技术边界",
        evidence_refs: ["candidate:q1"],
      }],
      priority_actions: [{
        action_id: "action-1",
        title: "补足缓存一致性权衡",
        why_it_matters: "这是当前最明显的技术缺口。",
        practice: "重答第二题，并比较两种一致性策略。",
        completion_criteria: "能说明选择条件和失败边界。",
        question_refs: ["q2"],
        evidence_refs: ["candidate:q2"],
      }],
      limitations: [{
        limitation_id: "coverage-limit",
        text: "有一道题未形成有效回答。",
        reason_code: "partial_coverage",
      }],
      evidence_refs: [
        { evidence_ref_id: "candidate:q1", question_id: "q1" },
        { evidence_ref_id: "candidate:q2", question_id: "q2" },
      ],
      technical_appendix: { reason_codes: ["partial_coverage"] },
      feedbacks: [
        feedback("q1", "如何定位线上延迟？", 82),
        feedback("q2", "如何选择缓存一致性策略？", 80),
      ],
    },
  },
  latest_job: {
    job_id: "job-failed-rescore",
    status: "failed",
    job_kind: "rescore",
    error_code: "provider_timeout",
  },
};

beforeEach(() => {
  window.history.replaceState({}, "", "/report-detail?session_id=session-1");
  downloadFile.mockResolvedValue(undefined);
  getJson.mockImplementation((path) => {
    if (path.endsWith("/report")) return Promise.resolve(reportResponse);
    if (path.endsWith("/reports")) {
      return Promise.resolve({
        items: [
          { report_id: "report-1", revision: 1, created_at: "2026-08-05T07:30:00Z", active: false },
          reportResponse.active_artifact,
        ],
      });
    }
    if (path.includes("question-evaluations")) {
      return Promise.resolve({ items: [{ question_id: "q2", status: "completed", retrieval_path: "vector" }] });
    }
    if (path.includes("agent-runs") || path.includes("runtime-events")) {
      return Promise.resolve({ items: [] });
    }
    throw new Error(`unexpected request: ${path}`);
  });
});

afterEach(() => cleanup());

describe("ReportDetailPage candidate information architecture", () => {
  it("keeps six candidate sections primary and diagnostics folded", async () => {
    render(<ReportDetailPage />);

    const primaryHeadings = [
      await screen.findByRole("heading", { name: "01 · 本轮结论与评分状态" }),
      screen.getByRole("heading", { name: "02 · 覆盖度和限制" }),
      screen.getByRole("heading", { name: "03 · 主要优势" }),
      screen.getByRole("heading", { name: "04 · Top 1–3 改进动作" }),
      screen.getByRole("heading", { name: "05 · 逐题证据与回答建议" }),
      screen.getByRole("heading", { name: "06 · 评估限制" }),
    ];
    primaryHeadings.forEach((heading) => expect(heading.closest("details")).toBeNull());

    const appendix = screen.getByText("技术附录").closest("details");
    expect(appendix).not.toHaveAttribute("open");
    expect(appendix).toHaveTextContent("逐题评审与检索路径");
    expect(appendix).toHaveTextContent("Agent 执行与运行事件");
    expect(appendix).toHaveTextContent("Report Artifact");
    expect(appendix).toHaveTextContent("partial_coverage");
  });

  it("keeps the active revision visible after a failed update", async () => {
    render(<ReportDetailPage />);

    expect(await screen.findByText("新版本处理失败，当前版本仍可使用")).toBeInTheDocument();
    expect(screen.getAllByText("第 2 版").length).toBeGreaterThan(0);
    expect(screen.getByText("本轮回答技术边界清楚，但覆盖尚不完整。")).toBeInTheDocument();
    expect(screen.getAllByText("部分评分").length).toBeGreaterThan(0);
    expect(screen.getAllByText("部分覆盖").length).toBeGreaterThan(0);
  });

  it("jumps from an action to its evidence question and opens it", async () => {
    render(<ReportDetailPage />);

    const jump = await screen.findByRole("link", { name: "查看对应题目" });
    const target = document.getElementById("question-q2");
    expect(target).not.toHaveAttribute("open");

    fireEvent.click(jump);

    await waitFor(() => expect(target).toHaveAttribute("open"));
    expect(target.querySelector("summary")).toHaveFocus();
  });

  it("does not display a numeric overall score for an unscored artifact", async () => {
    const unscored = structuredClone(reportResponse);
    Object.assign(unscored.active_artifact, {
      score_status: "unscored",
      score_reason_code: "insufficient_evidence",
      coverage_status: "none",
      overall_score: null,
      overall_dimension_scores: {
        breadth: null,
        depth: null,
        architecture: null,
        engineering: null,
        communication: null,
      },
      evaluated_count: 0,
      total_eligible_count: 3,
    });
    getJson.mockImplementation((path) => {
      if (path.endsWith("/report")) return Promise.resolve(unscored);
      if (path.endsWith("/reports")) return Promise.resolve({ items: [unscored.active_artifact] });
      return Promise.resolve({ items: [] });
    });

    render(<ReportDetailPage />);

    expect((await screen.findAllByLabelText(/综合评分未发布/)).length).toBeGreaterThan(0);
    expect(screen.getByText("证据不足，未发布数字")).toBeInTheDocument();
    expect(screen.getAllByText("未评分").length).toBeGreaterThan(0);
    expect(screen.getAllByText("无有效覆盖").length).toBeGreaterThan(0);
  });

  it("downloads the active PDF by immutable report ID and revision", async () => {
    render(<ReportDetailPage />);

    fireEvent.click(await screen.findByRole("button", { name: "下载完整报告" }));

    await waitFor(() => expect(downloadFile).toHaveBeenCalledWith(
      "/api/reports/report-2.pdf",
      "interview-report-r2-report-2.pdf",
    ));
  });

  it("downloads a historical revision without following the active pointer", async () => {
    render(<ReportDetailPage />);

    fireEvent.click(await screen.findByText("技术附录"));
    fireEvent.click(screen.getByRole("button", { name: "下载第 1 版" }));

    await waitFor(() => expect(downloadFile).toHaveBeenCalledWith(
      "/api/reports/report-1.pdf",
      "interview-report-r1-report-1.pdf",
    ));
  });
});

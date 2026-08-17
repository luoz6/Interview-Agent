import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";

const materialsCss = readFileSync(resolve("src/styles/pages/materials.css"), "utf8");

const READY_ID = "11111111-1111-4111-8111-111111111111";
const FAILED_ID = "22222222-2222-4222-8222-222222222222";

function material(overrides = {}) {
  return {
    document_id: READY_ID,
    display_name: "系统设计复盘",
    media_type: "text/markdown",
    size_bytes: 2450,
    status: "ready",
    enabled: true,
    allowed_usage: ["question", "follow_up", "feedback"],
    created_at: "2026-08-15T08:00:00Z",
    updated_at: "2026-08-15T08:00:00Z",
    error_code: null,
    ...overrides,
  };
}

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => payload,
  });
}

async function submitTextUpload(user, filename = "notes.txt") {
  await user.click(screen.getByRole("button", { name: /^上传资料$/ }));
  await user.upload(
    screen.getByLabelText(/点击选择，或将文件拖到这里/),
    new File(["interview notes"], filename, { type: "text/plain" }),
  );
  await user.click(screen.getByRole("button", { name: "上传并处理" }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  window.history.replaceState({}, "", "/materials");
});

describe("MaterialsPage", () => {
  it("renders compact safe rows, every item state, always-visible failed retry and search", async () => {
    const items = [
      material({
        owner_principal_id: "private-owner-value",
        revision_id: "private-revision-value",
        content_sha256: "private-hash-value",
        embedding: [0.1, 0.2],
        manifest: "private-manifest-value",
        corpus: "private-corpus-value",
        original_path: "/private/raw/path",
        extracted_content: "private raw content",
        internal_stage: "private-stage-value",
      }),
      material({ document_id: "33333333-3333-4333-8333-333333333333", display_name: "项目追问清单", status: "processing" }),
      material({ document_id: FAILED_ID, display_name: "缓存事故记录", status: "failed", enabled: false, error_code: "processing_failed" }),
      material({ document_id: "44444444-4444-4444-8444-444444444444", display_name: "岗位能力清单", status: "disabled", enabled: false }),
      material({ document_id: "55555555-5555-4555-8555-555555555555", display_name: "待删除笔记", status: "deleting", enabled: false }),
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(await response({ items })));
    const user = userEvent.setup();

    render(<MaterialsPage />);

    expect(await screen.findByRole("heading", { level: 1, name: "我的资料" })).toBeInTheDocument();
    expect(screen.getByText("你主动上传的文件，可在准备面试时选择使用")).toBeInTheDocument();
    expect(screen.getByText("只有在准备页选中的资料，才会用于对应面试。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "5 份资料" })).toBeInTheDocument();
    expect(screen.getByLabelText("资料概览")).toHaveTextContent("已就绪1份可选择处理中1份正在建立索引需处理1份需要你确认");
    for (const label of ["已就绪", "处理中", "处理失败", "已停用", "删除中"]) {
      expect(screen.getByRole("status", { name: `状态：${label}` })).toBeInTheDocument();
    }
    expect(screen.getAllByText("用于：定制问题 · 生成追问 · 辅助反馈")).toHaveLength(5);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /改名|停用|启用|永久删除/ })).not.toBeInTheDocument();
    expect(screen.getByText("资料处理暂时失败，请稍后重试。")).toBeInTheDocument();
    const failedRow = screen.getByText("缓存事故记录").closest("li");
    expect(within(failedRow).getByRole("button", { name: /重试/ })).toBeVisible();
    expect(within(failedRow).getByRole("button", { name: "更多操作" })).toHaveAttribute("aria-expanded", "false");
    for (const lockedName of ["项目追问清单", "待删除笔记"]) {
      const lockedRow = screen.getByText(lockedName).closest("li");
      expect(within(lockedRow).getByRole("button", { name: "更多操作" })).toBeDisabled();
    }

    await user.type(screen.getByRole("searchbox", { name: "搜索资料" }), "缓存");
    expect(screen.getByText("缓存事故记录")).toBeInTheDocument();
    expect(screen.queryByText("系统设计复盘")).not.toBeInTheDocument();
    expect(screen.getByText("当前显示 1 份匹配结果")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByText("系统设计复盘")).toBeInTheDocument();

    const dom = document.body.textContent.toLowerCase();
    for (const forbidden of [
      "owner_principal_id",
      "principal",
      "revision_id",
      "content_sha256",
      "embedding",
      "manifest",
      "corpus",
      "/private/raw/path",
      "private raw content",
      "private-stage-value",
    ]) {
      expect(dom).not.toContain(forbidden);
    }
  });

  it("keeps one inline disclosure open and restores trigger focus on Escape or explicit collapse", async () => {
    const second = material({
      document_id: "33333333-3333-4333-8333-333333333333",
      display_name: "项目追问清单",
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(await response({ items: [material(), second] })));
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const firstRow = (await screen.findByText("系统设计复盘")).closest("li");
    const secondRow = screen.getByText("项目追问清单").closest("li");
    const firstTrigger = within(firstRow).getByRole("button", { name: "更多操作" });
    const secondTrigger = within(secondRow).getByRole("button", { name: "更多操作" });
    const firstPanelId = firstTrigger.getAttribute("aria-controls");
    const secondPanelId = secondTrigger.getAttribute("aria-controls");
    const firstPanel = document.getElementById(firstPanelId);
    const secondPanel = document.getElementById(secondPanelId);

    expect(firstTrigger).toHaveAttribute("aria-expanded", "false");
    expect(firstPanelId).toBeTruthy();
    expect(secondPanelId).toBeTruthy();
    expect(secondPanelId).not.toBe(firstPanelId);
    expect(firstPanelId).toMatch(/^materials-management-[^\s]+$/);
    expect(firstPanel).toHaveAttribute("hidden");
    expect(firstPanel.querySelector("button[type='submit']")).not.toBeVisible();
    expect(firstTrigger.querySelector(".materials-disclosure-indicator svg")).toBeInTheDocument();
    expect(firstTrigger).not.toHaveTextContent(/[+−]/);
    await user.click(firstTrigger);

    expect(firstTrigger).toHaveAttribute("aria-expanded", "true");
    expect(firstPanel).toHaveAttribute("id", firstPanelId);
    expect(firstPanel).not.toHaveAttribute("hidden");
    expect(firstPanel).not.toHaveAttribute("role", "menu");
    expect(within(firstRow).queryByRole("menu")).not.toBeInTheDocument();

    await user.click(secondTrigger);
    expect(firstTrigger).toHaveAttribute("aria-expanded", "false");
    expect(firstPanel).toHaveAttribute("hidden");
    expect(within(firstRow).queryByRole("region", { name: "系统设计复盘的更多操作" })).not.toBeInTheDocument();
    expect(secondTrigger).toHaveAttribute("aria-expanded", "true");
    expect(secondPanel).not.toHaveAttribute("hidden");

    await user.keyboard("{Escape}");
    expect(secondTrigger).toHaveAttribute("aria-expanded", "false");
    expect(secondPanel).toHaveAttribute("hidden");
    expect(secondTrigger).toHaveFocus();

    await user.click(secondTrigger);
    await user.click(within(secondRow).getByRole("button", { name: "收起管理操作" }));
    expect(secondTrigger).toHaveAttribute("aria-expanded", "false");
    expect(secondPanel).toHaveAttribute("hidden");
    expect(secondTrigger).toHaveFocus();
    expect(secondTrigger).toHaveAttribute("aria-controls", secondPanelId);

    const hiddenControls = [...secondPanel.querySelectorAll("button, input, select, textarea, a[href]")];
    await user.tab();
    expect(hiddenControls).not.toContain(document.activeElement);

    await user.click(firstTrigger);
    await user.type(screen.getByRole("searchbox", { name: "搜索资料" }), "系统");
    expect(firstTrigger).toHaveAttribute("aria-expanded", "false");
    expect(firstPanel).toHaveAttribute("hidden");
    expect(firstTrigger).toHaveAttribute("aria-controls", firstPanelId);
  });

  it("edits usage as a local draft, cancels without a request and saves with one PATCH", async () => {
    let current = material({ allowed_usage: ["feedback", "question", "follow_up"] });
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (!options.method) return response({ items: [current] });
      if (options.method === "PATCH" && url.endsWith(READY_ID)) {
        const payload = JSON.parse(options.body);
        current = { ...current, allowed_usage: payload.allowed_usage };
        return response(current);
      }
      throw new Error(`Unexpected request ${options.method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const row = (await screen.findByText("系统设计复盘")).closest("li");
    const trigger = within(row).getByRole("button", { name: "更多操作" });
    await user.click(trigger);
    const panel = within(row).getByRole("region", { name: "系统设计复盘的更多操作" });
    const feedback = within(panel).getByRole("checkbox", { name: "系统设计复盘：辅助反馈" });
    const saveUsage = within(panel).getByRole("button", { name: "保存用途" });

    expect(saveUsage).toBeDisabled();
    fireEvent.click(saveUsage);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(feedback);
    expect(feedback).not.toBeChecked();
    expect(saveUsage).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(within(panel).getByRole("button", { name: "取消" }));
    expect(feedback).toBeChecked();
    expect(saveUsage).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(feedback);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(saveUsage).toBeEnabled();
    fireEvent.click(saveUsage);
    fireEvent.click(saveUsage);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const patchCalls = fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH");
    expect(patchCalls).toHaveLength(1);
    expect(JSON.parse(patchCalls[0][1].body)).toEqual({ allowed_usage: ["question", "follow_up"] });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("用于：定制问题 · 生成追问")).toBeInTheDocument();
    expect(saveUsage).toBeDisabled();
  });

  it("preserves the usage draft and disclosure when saving fails", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [material()] }))
      .mockImplementationOnce(() => response({
        detail: { code: "invalid_request", message: "private validation detail" },
      }, 422));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const row = (await screen.findByText("系统设计复盘")).closest("li");
    const trigger = within(row).getByRole("button", { name: "更多操作" });
    await user.click(trigger);
    const panel = within(row).getByRole("region", { name: "系统设计复盘的更多操作" });
    const feedback = within(panel).getByRole("checkbox", { name: "系统设计复盘：辅助反馈" });
    await user.click(feedback);
    await user.click(within(panel).getByRole("button", { name: "保存用途" }));

    expect(await within(panel).findByRole("alert")).toHaveTextContent("用途保存失败：提交的资料信息不符合要求，请检查后重试。");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(feedback).not.toBeChecked();
    expect(within(panel).getByRole("button", { name: "保存用途" })).toBeEnabled();
    expect(within(row).getByRole("region", { name: "系统设计复盘的更多操作" })).toBeVisible();
    expect(document.body).not.toHaveTextContent("private validation detail");
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH")).toHaveLength(1);
  });

  it("keeps disclosure touch targets and motion within the reduced-motion contract", () => {
    expect(materialsCss).toMatch(/\.materials-disclosure-trigger\s*\{[\s\S]*?min-width:\s*var\(--start-control-height-touch\);[\s\S]*?min-height:\s*var\(--start-control-height-touch\);/);
    expect(materialsCss).toMatch(/\.materials-usage-options label\s*\{[\s\S]*?min-width:\s*var\(--start-control-height-touch\);[\s\S]*?min-height:\s*var\(--start-control-height-touch\);/);
    expect(materialsCss).toMatch(/animation:\s*materials-management-enter 200ms var\(--start-ease-out\) both;/);

    const animationStart = materialsCss.indexOf("@keyframes materials-management-enter");
    const animationEnd = materialsCss.indexOf("@keyframes materials-progress", animationStart);
    const managementAnimation = materialsCss.slice(animationStart, animationEnd);
    expect(managementAnimation).toContain("opacity");
    expect(managementAnimation).toContain("transform");
    expect(managementAnimation).not.toMatch(/(?:height|max-height|padding|margin|top|left)\s*:/);
    const indicatorStart = materialsCss.indexOf(".materials-disclosure-indicator {");
    const indicatorEnd = materialsCss.indexOf("}", indicatorStart);
    const indicatorRule = materialsCss.slice(indicatorStart, indicatorEnd);
    expect(indicatorRule).toContain("transition: transform 180ms");
    expect(indicatorRule).not.toContain("opacity");
    expect(materialsCss).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.materials-management-panel\s*\{\s*animation:\s*none;\s*\}/);
  });

  it("validates and uploads only file plus editable display_name through FormData", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [] }))
      .mockImplementationOnce((_url, options) => response(material({
        document_id: "66666666-6666-4666-8666-666666666666",
        display_name: options.body.get("display_name"),
        status: "ready",
      }), 201));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup({ applyAccept: false });
    render(<MaterialsPage />);

    await screen.findByRole("heading", { name: "0 份资料" });
    const uploadTrigger = screen.getByRole("button", { name: /^上传资料$/ });
    expect(uploadTrigger).toHaveAttribute("aria-expanded", "false");
    await user.click(uploadTrigger);
    expect(uploadTrigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("heading", { name: "上传一份新的参考资料" })).toBeInTheDocument();
    const uploadSteps = screen.getByRole("list", { name: "资料处理步骤" });
    for (const step of ["选择文件", "确认名称", "自动处理"]) {
      expect(within(uploadSteps).getByText(step)).toBeInTheDocument();
    }
    const fileInput = screen.getByLabelText(/点击选择，或将文件拖到这里/);

    await user.upload(fileInput, new File(["pdf"], "notes.pdf", { type: "application/pdf" }));
    expect(screen.getByRole("alert")).toHaveTextContent("仅支持 Markdown 或 TXT 文件");

    const markdown = new File(["# system design"], "system-design.md", { type: "text/markdown" });
    await user.upload(fileInput, markdown);
    const nameInput = screen.getByRole("textbox", { name: "资料名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "  分布式系统复盘  ");
    await user.click(screen.getByRole("button", { name: "上传并处理" }));

    expect(await screen.findByText("分布式系统复盘")).toBeInTheDocument();
    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/materials");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect([...options.body.keys()]).toEqual(["file", "display_name"]);
    expect(options.body.get("file")).toBe(markdown);
    expect(options.body.get("display_name")).toBe("分布式系统复盘");
    expect(options.headers).toBeUndefined();
    expect(screen.getByText("上传完成")).toBeInTheDocument();
    expect(screen.getByText("资料已上传并可以使用。")).toBeInTheDocument();
    expect(screen.queryByText("资料已上传，正在处理中。")).not.toBeInTheDocument();
  });

  it("keeps a failed upload in the list and reports that processing did not complete", async () => {
    const failedUpload = material({
      document_id: "66666666-6666-4666-8666-666666666666",
      display_name: "notes",
      status: "failed",
      error_code: "embedding_unavailable",
    });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [] }))
      .mockImplementationOnce(() => response(failedUpload, 201));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByRole("heading", { name: "0 份资料" });
    await submitTextUpload(user);

    expect(await screen.findByRole("status", { name: "状态：处理失败" })).toBeInTheDocument();
    expect(screen.getByText("资料已上传，但处理未完成。请查看失败原因后重试。")).toBeInTheDocument();
    expect(screen.queryByText("资料已上传并可以使用。")).not.toBeInTheDocument();
    expect(screen.queryByText("资料已上传，正在处理中。")).not.toBeInTheDocument();
  });

  it("treats an unexpected processing upload as in-flight and refreshes it manually", async () => {
    const documentId = "66666666-6666-4666-8666-666666666666";
    const processing = material({
      document_id: documentId,
      display_name: "notes",
      status: "processing",
    });
    const ready = { ...processing, status: "ready" };
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [] }))
      .mockImplementationOnce(() => response(processing, 201))
      .mockImplementationOnce(() => response({ items: [ready] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByRole("heading", { name: "0 份资料" });
    await submitTextUpload(user);

    expect(await screen.findByRole("status", { name: "状态：处理中" })).toBeInTheDocument();
    expect(screen.getByText("资料仍在处理中，请稍后手动刷新资料列表。")).toBeInTheDocument();
    expect(screen.queryByText("资料已上传并可以使用。")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "刷新列表" }));

    expect(await screen.findByRole("status", { name: "状态：已就绪" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("supports rename, usage, disable, enable, retry and confirmed permanent delete", async () => {
    let ready = material();
    let failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (!options.method) return response({ items: [ready, failed] });
      if (options.method === "PATCH" && url.endsWith(READY_ID)) {
        const payload = JSON.parse(options.body);
        ready = {
          ...ready,
          ...(payload.display_name ? { display_name: payload.display_name } : {}),
          ...(payload.allowed_usage ? { allowed_usage: payload.allowed_usage } : {}),
          ...(payload.enabled === false ? { enabled: false, status: "disabled" } : {}),
          ...(payload.enabled === true ? { enabled: true, status: "ready" } : {}),
        };
        return response(ready);
      }
      if (options.method === "POST" && url.endsWith(`${FAILED_ID}/retry`)) {
        failed = { ...failed, status: "ready", enabled: true, error_code: null };
        return response(failed);
      }
      if (options.method === "DELETE" && url.endsWith(READY_ID)) {
        return response({ document_id: READY_ID, deleted: true });
      }
      throw new Error(`Unexpected request ${options.method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByText("系统设计复盘");
    const readyRow = screen.getByText("系统设计复盘").closest("li");
    await user.click(within(readyRow).getByRole("button", { name: "更多操作" }));
    await user.click(within(readyRow).getByRole("button", { name: /改名/ }));
    const rename = within(readyRow).getByRole("textbox", { name: "新的资料名称" });
    await user.clear(rename);
    await user.type(rename, "系统设计精要");
    await user.click(within(readyRow).getByRole("button", { name: "保存名称" }));
    expect(await screen.findByText("系统设计精要")).toBeInTheDocument();
    expect(screen.getByText("操作已完成")).toBeInTheDocument();

    const patchCountAfterRename = fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH").length;
    const feedback = screen.getByRole("checkbox", { name: "系统设计精要：辅助反馈" });
    await user.click(feedback);
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH")).toHaveLength(patchCountAfterRename);
    await user.click(within(readyRow).getByRole("button", { name: "保存用途" }));
    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH");
      expect(JSON.parse(patchCalls.at(-1)[1].body)).toEqual({ allowed_usage: ["question", "follow_up"] });
      expect(patchCalls).toHaveLength(patchCountAfterRename + 1);
    });

    const renamedRow = screen.getByText("系统设计精要").closest("li");
    await user.click(within(renamedRow).getByRole("button", { name: /停用/ }));
    expect(await within(renamedRow).findByRole("button", { name: /启用/ })).toBeInTheDocument();
    await user.click(within(renamedRow).getByRole("button", { name: /启用/ }));
    expect(await within(renamedRow).findByRole("button", { name: /停用/ })).toBeInTheDocument();

    const failedRow = screen.getByText("失败资料").closest("li");
    await user.click(within(failedRow).getByRole("button", { name: /重试/ }));
    expect(await within(failedRow).findByRole("status", { name: "状态：已就绪" })).toBeInTheDocument();
    expect(screen.getByText("重新处理完成")).toBeInTheDocument();
    expect(screen.getByText("资料已重新处理并可以使用。")).toBeInTheDocument();

    await user.click(within(renamedRow).getByRole("button", { name: /永久删除/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("将永久删除该资料的原始文件和索引，且不可恢复");
    await user.click(within(dialog).getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(screen.queryByText("系统设计精要")).not.toBeInTheDocument());

    expect(fetchMock.mock.calls.some(([url, options]) => (
      url === `/api/materials/${READY_ID}` && options.method === "DELETE"
    ))).toBe(true);
  });

  it("keeps retry failure visible without claiming that reprocessing completed", async () => {
    const failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [failed] }))
      .mockImplementationOnce(() => response(failed));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const failedRow = (await screen.findByText("失败资料")).closest("li");
    await user.click(within(failedRow).getByRole("button", { name: /重试/ }));

    expect(await screen.findByText("重新处理仍未完成，请检查失败原因。")).toBeInTheDocument();
    expect(within(failedRow).getByRole("status", { name: "状态：处理失败" })).toBeInTheDocument();
    expect(screen.queryByText("资料已重新处理并可以使用。")).not.toBeInTheDocument();
  });

  it("treats an unexpected processing retry as in-flight until manual refresh", async () => {
    const failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const processing = { ...failed, status: "processing", error_code: null };
    const ready = { ...processing, status: "ready", enabled: true };
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [failed] }))
      .mockImplementationOnce(() => response(processing))
      .mockImplementationOnce(() => response({ items: [ready] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const failedRow = (await screen.findByText("失败资料")).closest("li");
    const managementTrigger = within(failedRow).getByRole("button", { name: "更多操作" });
    await user.click(managementTrigger);
    const managementPanel = within(failedRow).getByRole("region", { name: "失败资料的更多操作" });
    await user.click(within(failedRow).getByRole("button", { name: /重试/ }));

    expect(await screen.findByText("资料仍在处理中，请稍后手动刷新资料列表。")).toBeInTheDocument();
    expect(within(failedRow).getByRole("status", { name: "状态：处理中" })).toBeInTheDocument();
    expect(managementTrigger).toBeDisabled();
    expect(within(managementPanel).getByRole("button", { name: "收起管理操作" })).toBeEnabled();
    await user.click(within(managementPanel).getByRole("button", { name: "收起管理操作" }));
    expect(managementPanel).toHaveAttribute("hidden");
    expect(screen.queryByText("资料已重新处理并可以使用。")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "刷新列表" }));
    expect(await screen.findByRole("status", { name: "状态：已就绪" })).toBeInTheDocument();
  });

  it("silently preserves items and capability when upload and retry requests are aborted", async () => {
    const failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const abort = () => Promise.reject(new DOMException("aborted", "AbortError"));
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [failed] }))
      .mockImplementationOnce(abort)
      .mockImplementationOnce(abort);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const failedRow = (await screen.findByText("失败资料")).closest("li");
    const retryButton = within(failedRow).getByRole("button", { name: /重试/ });
    await user.click(retryButton);
    await waitFor(() => expect(retryButton).toBeEnabled());

    await submitTextUpload(user);
    await waitFor(() => expect(screen.getByRole("button", { name: "上传并处理" })).toBeEnabled());

    expect(screen.getByText("失败资料")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^上传资料$/ })).toBeEnabled();
    expect(screen.queryByText("资料上传与重新处理当前未启用。", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("资料已重新处理并可以使用。")).not.toBeInTheDocument();
    expect(screen.queryByText("重新处理仍未完成，请检查失败原因。")).not.toBeInTheDocument();
    expect(screen.queryByText("资料已上传并可以使用。")).not.toBeInTheDocument();
    expect(screen.queryByText("资料已上传，但处理未完成。请查看失败原因后重试。")).not.toBeInTheDocument();
  });

  it("shows capability-disabled copy for a hidden list without exposing the raw error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response({
      detail: { code: "not_found", message: "private route detail" },
    }, 404)));
    render(<MaterialsPage />);

    expect(await screen.findByRole("heading", { name: "资料功能当前未启用" })).toBeInTheDocument();
    expect(screen.queryByText("private route detail")).not.toBeInTheDocument();
  });

  it("keeps permanent deletion available when upload capability is hidden", async () => {
    const existing = material();
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [existing] }))
      .mockImplementationOnce(() => response({ detail: { code: "not_found", message: "private" } }, 404))
      .mockImplementationOnce(() => response({ document_id: READY_ID, deleted: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByText("系统设计复盘");
    await user.click(screen.getByRole("button", { name: /^上传资料$/ }));
    const fileInput = screen.getByLabelText(/点击选择，或将文件拖到这里/);
    await user.upload(fileInput, new File(["notes"], "notes.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "上传并处理" }));

    expect(await screen.findByText("已有资料仍可管理或永久删除。", { exact: false })).toBeInTheDocument();
    const row = screen.getByText("系统设计复盘").closest("li");
    await user.click(within(row).getByRole("button", { name: "更多操作" }));
    const deleteButton = within(row).getByRole("button", { name: /永久删除/ });
    expect(deleteButton).toBeEnabled();
    await user.click(deleteButton);
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(screen.queryByText("系统设计复盘")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.at(-1)[1].method).toBe("DELETE");
  });

  it("marks retry capability unavailable without changing the failed item", async () => {
    const failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [failed] }))
      .mockImplementationOnce(() => response({
        detail: { code: "not_found", message: "private route detail" },
      }, 404));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    const failedRow = (await screen.findByText("失败资料")).closest("li");
    await user.click(within(failedRow).getByRole("button", { name: /重试/ }));

    expect(await screen.findByText("已有资料仍可管理或永久删除。", { exact: false })).toBeInTheDocument();
    expect(within(failedRow).getByRole("status", { name: "状态：处理失败" })).toBeInTheDocument();
    expect(within(failedRow).getByRole("button", { name: /重试/ })).toBeDisabled();
    expect(screen.queryByText("private route detail")).not.toBeInTheDocument();
  });
});

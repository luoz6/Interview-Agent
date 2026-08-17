import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { LegacyRouteRedirect } from "./components/LegacyRouteRedirect";
import { MobileNav } from "./components/MobileNav";
import { PrimaryNav } from "./components/PrimaryNav";
import { RagConsoleShell } from "./components/rag/RagConsoleShell";
import {
  canonicalRagLabPath,
  RAG_LAB_PATHS,
  RAG_LAB_ROUTES,
} from "./rag/ragRoutes";

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => payload,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("lazy loads the canonical Materials route", async () => {
  const fetchMock = vi.fn().mockImplementation((url) => {
    expect(url).toBe("/api/materials");
    return response({ items: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  window.history.replaceState({}, "", "/materials");

  render(<App />);

  expect(await screen.findByRole("heading", { level: 1, name: "我的资料" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "0 份资料" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("routes the canonical lab overview without changing the RAG API path", async () => {
  const fetchMock = vi.fn().mockImplementation((url) => {
    expect(url).toBe("/api/rag/overview");
    return response({
      current_engine: "legacy",
      comparison_engines: ["legacy", "hybrid-v2"],
      corpus: { version: "v1", chunk_count: 0, manifest_sha256: "" },
      embedding: { provider: "local", model: "demo", revision: "v1", dimension: 3 },
      component_versions: {},
      technologies: [],
      profiles: [],
      experiment_findings: [],
      demo_boundaries: [],
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  window.history.replaceState({}, "", RAG_LAB_ROUTES.overview);

  render(<App />);

  expect(await screen.findByRole("heading", { name: "RAG 工程概览" })).toBeInTheDocument();
  expect(screen.getAllByText("AI 技术实验室").length).toBeGreaterThan(0);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("keeps product navigation ordered and free of engineering entries", () => {
  render(
    <>
      <PrimaryNav pathname="/materials" />
      <MobileNav pathname="/materials" />
    </>,
  );

  const expected = ["准备", "报告", "我的资料", "我的记忆", "帮助"];
  const primary = screen.getByRole("navigation", { name: "主导航" });
  const mobile = screen.getByRole("navigation", { name: "移动端主导航" });
  expect(within(primary).getAllByRole("link").map((link) => link.textContent)).toEqual(expected);
  expect(within(mobile).getAllByRole("link").map((link) => link.textContent)).toEqual(expected);
  expect(within(primary).getByRole("link", { name: "我的资料" })).toHaveAttribute("href", "/materials");
  expect(within(primary).getByRole("link", { name: "我的资料" })).toHaveAttribute("aria-current", "page");
  expect(document.body.textContent).not.toMatch(/RAG|RRF|Corpus|Evaluation|Evidence Trace/i);
});

it("maps every legacy RAG path once and preserves search and hash in the redirect", async () => {
  const expected = {
    "/rag": RAG_LAB_ROUTES.overview,
    "/rag/retrieval": RAG_LAB_ROUTES.retrieval,
    "/rag/evaluation": RAG_LAB_ROUTES.evaluation,
    "/rag/evidence-trace": RAG_LAB_ROUTES.evidenceTrace,
    "/rag/corpus": RAG_LAB_ROUTES.corpus,
  };
  expect(Object.keys(expected).map(canonicalRagLabPath)).toEqual(Object.values(expected));
  expect(canonicalRagLabPath(RAG_LAB_ROUTES.overview)).toBeNull();
  expect(canonicalRagLabPath("/rag/unknown")).toBeNull();

  const navigate = vi.fn();
  window.history.replaceState({}, "", "/rag/retrieval?artifact=frozen#results");
  render(<LegacyRouteRedirect to={RAG_LAB_ROUTES.retrieval} navigate={navigate} />);

  await waitFor(() => expect(navigate).toHaveBeenCalledWith(
    `${RAG_LAB_ROUTES.retrieval}?artifact=frozen#results`,
  ));
  expect(screen.getByRole("link", { name: "打开 AI 技术实验室" })).toHaveAttribute(
    "href",
    `${RAG_LAB_ROUTES.retrieval}?artifact=frozen#results`,
  );

  cleanup();
  navigate.mockClear();
  window.history.replaceState({}, "", RAG_LAB_ROUTES.retrieval);
  render(<LegacyRouteRedirect to={RAG_LAB_ROUTES.retrieval} navigate={navigate} />);
  expect(navigate).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "返回我的资料" })).toHaveAttribute("href", "/materials");
});

it("uses only canonical lab links in the technical subnavigation", () => {
  window.history.replaceState({}, "", RAG_LAB_ROUTES.evaluation);
  render(<RagConsoleShell><p>实验内容</p></RagConsoleShell>);

  const navigation = screen.getByRole("navigation", { name: "AI 技术实验室导航" });
  const links = within(navigation).getAllByRole("link");
  expect(links.map((link) => link.getAttribute("href"))).toEqual(RAG_LAB_PATHS);
  expect(within(navigation).getByRole("link", { name: "评测看板" })).toHaveAttribute("aria-current", "page");
  expect(links.every((link) => link.getAttribute("href").startsWith("/rag/lab"))).toBe(true);
});

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { HelpPage } from "./HelpPage";

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

it("explains the complete User Materials product boundary without promoting maintainer tools", () => {
  window.history.replaceState({}, "", "/help");
  render(<HelpPage />);

  expect(screen.getByRole("heading", { level: 2, name: "使用“我的资料”" })).toBeInTheDocument();
  expect(screen.getByText(/UTF-8 Markdown 或 TXT；单个文件最大 1 MiB/)).toBeInTheDocument();
  expect(screen.getByText(/当前不支持 PDF 或 DOCX 上传/)).toBeInTheDocument();
  expect(screen.getByText(/“已就绪”可以选择；“处理中”需要等待；“处理失败”可以重试；“已停用”需要先重新启用/)).toBeInTheDocument();
  expect(screen.getByText(/只有已就绪且已启用的资料能加入本次面试/)).toBeInTheDocument();
  expect(screen.getByText(/选择资料只表示允许使用，不表示已经参考/)).toBeInTheDocument();
  expect(screen.getByText(/删除后，历史引用只显示“已删除资料”/)).toBeInTheDocument();
  expect(screen.getByText(/不会改变评分规则、权重或及格线/)).toBeInTheDocument();
  expect(screen.getByText(/没有资料引用也不表示自动扣分/)).toBeInTheDocument();
  expect(screen.getByText(/“我的资料”是你上传并为某次面试选择的文件/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /打开我的资料/ })).toHaveAttribute("href", "/materials");
  expect(screen.getByRole("link", { name: /打开 AI 技术实验室/ })).toHaveAttribute("href", "/rag/lab");

  const primary = screen.getByRole("navigation", { name: "主导航" });
  expect(within(primary).getAllByRole("link").map((link) => link.textContent)).toEqual([
    "准备",
    "报告",
    "我的资料",
    "我的记忆",
    "帮助",
  ]);
  expect(within(primary).queryByText(/RAG|RRF|Corpus|技术实验室/i)).not.toBeInTheDocument();
});

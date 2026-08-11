import { gzipSync } from "node:zlib";
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const DIST_DIR = resolve(process.cwd(), "dist");
const MANIFEST_PATH = join(DIST_DIR, ".vite", "manifest.json");
const OUTPUT_PATH = join(DIST_DIR, "bundle-summary.json");
const KIB = 1024;

const budgets = Object.freeze({
  initialJavaScriptGzipBytes: 66 * KIB,
  initialCssGzipBytes: 20 * KIB,
});

const routeModules = Object.freeze({
  prep: "src/pages/StartPage.jsx",
  interview: "src/pages/InterviewPage.jsx",
  reportProcessing: "src/pages/ReportProcessingPage.jsx",
  reportDetail: "src/pages/ReportDetailPage.jsx",
  reports: "src/pages/ReportsPage.jsx",
  help: "src/pages/HelpPage.jsx",
  memoryCenter: "src/pages/MemoryCenterPage.jsx",
});

function fail(message) {
  throw new Error(`[bundle-budget] ${message}`);
}

function readJson(path, label) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    fail(`${label} cannot be read: ${error.message}`);
  }
}

function assetMetrics(relativePath) {
  const absolutePath = join(DIST_DIR, relativePath);
  let contents;
  try {
    contents = readFileSync(absolutePath);
  } catch (error) {
    fail(`manifest asset ${relativePath} is missing: ${error.message}`);
  }
  return {
    file: relativePath.replaceAll("\\", "/"),
    bytes: contents.byteLength,
    gzipBytes: gzipSync(contents, { level: 9 }).byteLength,
  };
}

function collectStaticChunkKeys(manifest, rootKey) {
  const visited = new Set();
  const visit = (key) => {
    if (visited.has(key)) return;
    const chunk = manifest[key];
    if (!chunk) fail(`manifest import ${key} is unresolved`);
    visited.add(key);
    for (const importedKey of chunk.imports || []) visit(importedKey);
  };
  visit(rootKey);
  return visited;
}

function summarizeGraph(manifest, rootKey) {
  const chunkKeys = collectStaticChunkKeys(manifest, rootKey);
  const jsFiles = new Set();
  const cssFiles = new Set();
  for (const key of chunkKeys) {
    const chunk = manifest[key];
    if (chunk.file?.endsWith(".js")) jsFiles.add(chunk.file);
    for (const cssFile of chunk.css || []) cssFiles.add(cssFile);
  }
  const javascript = [...jsFiles].sort().map(assetMetrics);
  const css = [...cssFiles].sort().map(assetMetrics);
  const total = (assets, field) => assets.reduce((sum, asset) => sum + asset[field], 0);
  return {
    source: rootKey,
    chunkSources: [...chunkKeys].sort(),
    javascript,
    css,
    totals: {
      javascriptBytes: total(javascript, "bytes"),
      javascriptGzipBytes: total(javascript, "gzipBytes"),
      cssBytes: total(css, "bytes"),
      cssGzipBytes: total(css, "gzipBytes"),
    },
  };
}

const manifest = readJson(MANIFEST_PATH, "Vite manifest");
const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
if (!entryKey) fail("Vite manifest has no entry chunk");

const initial = summarizeGraph(manifest, entryKey);
const routes = {};
for (const [routeName, moduleKey] of Object.entries(routeModules)) {
  if (!manifest[moduleKey]) fail(`route module ${moduleKey} is absent from the manifest`);
  if (!manifest[moduleKey].isDynamicEntry) fail(`route module ${moduleKey} is not a lazy dynamic entry`);
  routes[routeName] = summarizeGraph(manifest, moduleKey);
}

const forbiddenInitialSources = [
  routeModules.reportDetail,
  routeModules.reports,
  routeModules.help,
];
const eagerSources = new Set(initial.chunkSources);
for (const source of forbiddenInitialSources) {
  if (eagerSources.has(source)) fail(`${source} leaked into the initial route graph`);
}

const checks = {
  initialJavaScriptWithinBudget:
    initial.totals.javascriptGzipBytes <= budgets.initialJavaScriptGzipBytes,
  initialCssWithinBudget:
    initial.totals.cssGzipBytes <= budgets.initialCssGzipBytes,
  protectedRoutesRemainLazy: forbiddenInitialSources.every((source) => !eagerSources.has(source)),
};

const summary = {
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  budgets,
  checks,
  initial,
  routes,
};

writeFileSync(OUTPUT_PATH, `${JSON.stringify(summary, null, 2)}\n`, "utf8");

if (!checks.initialJavaScriptWithinBudget) {
  fail(`initial JavaScript is ${initial.totals.javascriptGzipBytes} bytes gzip; budget is ${budgets.initialJavaScriptGzipBytes}`);
}
if (!checks.initialCssWithinBudget) {
  fail(`initial CSS is ${initial.totals.cssGzipBytes} bytes gzip; budget is ${budgets.initialCssGzipBytes}`);
}
if (!checks.protectedRoutesRemainLazy) fail("one or more protected routes are no longer lazy");

process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

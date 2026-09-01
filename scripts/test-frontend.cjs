"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appPath = path.join(__dirname, "..", "frontend", "app.js");
const source = `${fs.readFileSync(appPath, "utf8")}
globalThis.frontendUnderTest = { formatPlanSetSummary, PLANNING_RESULT_LIMIT };`;
const context = {
  document: { addEventListener() {} },
  localStorage: { getItem() { return null; } },
};
vm.createContext(context);
vm.runInContext(source, context, { filename: appPath });

const { formatPlanSetSummary, PLANNING_RESULT_LIMIT } = context.frontendUnderTest;
const plans = (count) => Array.from({ length: count }, () => ({}));

test("requests at most ten planning results", () => {
  assert.equal(PLANNING_RESULT_LIMIT, 10);
  assert.match(source, /max_solutions:\s*PLANNING_RESULT_LIMIT/);
});

test("labels an exhaustively enumerated result as complete", () => {
  const summary = formatPlanSetSummary({
    status: "optimal",
    plans: plans(4),
    plan_limit: 10,
    all_plans_returned: true,
    plans_truncated: false,
  });
  assert.equal(summary.kind, "complete");
  assert.equal(summary.text, "已列出全部 4 种可行排课方式");
});

test("labels an optimal truncated result as the recommended top ten", () => {
  const summary = formatPlanSetSummary({
    status: "optimal",
    plans: plans(10),
    plan_limit: 10,
    all_plans_returned: false,
    plans_truncated: true,
  });
  assert.equal(summary.kind, "truncated");
  assert.equal(summary.text, "可行组合超过 10 种，已按推荐顺序列出前 10 种");
});

test("does not claim global recommendation order after a timeout", () => {
  const summary = formatPlanSetSummary({
    status: "feasible_timeout",
    plans: plans(10),
    plan_limit: 10,
    all_plans_returned: false,
    plans_truncated: true,
  });
  assert.equal(summary.kind, "unknown");
  assert.equal(summary.text, "已找到 10 种可行排课方式；求解受时限影响，未确认是否为全局前 10 种");
});

test("uses cautious wording when a new solve cannot prove whether more plans exist", () => {
  const summary = formatPlanSetSummary({
    status: "feasible_timeout",
    plans: plans(6),
    plan_limit: 10,
    all_plans_returned: false,
    plans_truncated: false,
  });
  assert.equal(summary.kind, "unknown");
  assert.equal(summary.text, "已找到 6 种可行排课方式；求解受时限影响，未确认是否还有更多组合");
});

test("only describes inconclusive results as recommended when optimization completed", () => {
  const summary = formatPlanSetSummary({
    status: "optimal",
    plans: plans(6),
    plan_limit: 10,
    all_plans_returned: false,
    plans_truncated: false,
  });
  assert.equal(summary.kind, "unknown");
  assert.equal(summary.text, "已按推荐顺序列出 6 种可行排课方式；当前求解未能确认是否还有更多组合");
});

test("keeps old history readable without claiming exhaustive enumeration", () => {
  const summary = formatPlanSetSummary({ status: "optimal", plans: plans(5) });
  assert.equal(summary.kind, "unknown");
  assert.equal(summary.text, "此历史结果包含 5 种可行排课方式；旧记录未保存是否已经列完");
});

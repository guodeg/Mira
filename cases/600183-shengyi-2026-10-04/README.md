# 600183 生益科技 — Research Case

**case_id** `600183-shengyi-2026-10-04` ｜ **depth** deep_dive ｜ **scope** CN
**cutoff** 2026-10-04

## 交付物

| 文件 | 内容 |
| --- | --- |
| [investment-memo.md](investment-memo.md) | 研报正文：五模块数据链、产业链上下游验证、期望地图、刷新条件、证据缺口 |
| [evidence-log.csv](evidence-log.csv) | 21 条事实条目（canonical v1.2 schema，带 source_id / authority_level / source_date / as_of_date / stale 语义） |
| [routing.json](routing.json) | 路由、基准选择、预算、已知缺口、待验证假设、刷新与失效条件 |
| [case-notes.md](case-notes.md) | 框架选择理由、自我修正记录、工具问题与处理、协议边界 |

## 一句话结论（事实层）

覆铜板与制板同处一条 AI 算力链，但**盈利结构存在代差**（生益毛利率 26.47% / ROE 21.25% vs 下游 35.5% / 28.6–35.6%），**而市场给三家的 PS 定价几乎相同**（9.08 / 9.24 / 9.40）——市场按收入与赛道定价，未按资本回报率区分。

## 复现方式

```bash
export PYTHONPATH=tools
# 估值 / 盈利 / 预期 / 筹码 / 技术（基准自动路由为 000300.SS 并标注）
for c in 600183 002463 300476; do
  python -m mira_data fetch valuation_snapshot   $c --out private/dd-val-$c  --as-of 2026-10-04
  python -m mira_data fetch consensus_estimate   $c --out private/dd-cons-$c --as-of 2026-10-04
  python -m mira_data fetch executive_holdings   $c --out private/dd-eh-$c   --as-of 2026-10-04
  python -m mira_data fetch margin_balance       $c --out private/dd-mb-$c   --as-of 2026-10-04
  python -m mira_data technical                  $c --out private/peer-$c    --as-of 2026-10-04
done
```

## 协议边界

只输出客观事实推演与不对称性分析。**不含仓位、买卖或择时建议** —— 用户持仓、权重、授权与风险预算未知，按 `OPERATING_CONTRACT.md` 不得输出组合结论。

## 刷新与失效条件（refresh policy）

| 字段 | 值 |
| --- | --- |
| `research_cutoff_date` / `as_of` | 2026-10-04 |
| `stale_after` | 技术面 30 天；估值与一致预期 90 天；财务至下一报告期 |
| `must_refresh_if` | ① 生益披露高速覆铜板分产品毛利或收入结构；② 任一标的收盘跌破 invalidation（生益 102.49 / 沪电 102.01）；③ 胜宏跌破 208.43 支撑（将验证需求斜率放缓假说）；④ 任一制板端毛利率显著低于 35%（将削弱"下游需求未放缓"读数） |

`next refresh` 建议：2026Q3 报告期披露后立即重跑（关键验证点 H1 vs H2）。

## 协议边界与免责声明

**本报告不构成投资建议**（does not constitute investment advice）。输出为客观事实推演与不对称性风险分析，**不含仓位建议、买卖建议或择时判断** —— 用户持仓、权重、授权与风险预算未知，按 `OPERATING_CONTRACT.md` 不得输出组合结论。

## 必读限制

本报告有 8 项证据缺口，其中**分产品/分等级毛利率的缺失**使核心命题（材料端议价权归属）无法裁决。结论强度为 `working_view`，L5 证据占 17/21，用于耐久判断前需与 L1/L2 交叉核验。详见 memo §6 与 manifest 的 `blocking_gaps`。

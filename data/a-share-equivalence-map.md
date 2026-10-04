# A 股数据本地化对照表 (US ↔ CN Equivalence Map)

Mira 的原始形态是美股研究协议，数据层最初的锚点是 SEC / FRED / BEA / FINRA / CBOE。这份表把
每一类**已实现的美国通道**对上 A 股等价物，并标出哪些等价物已接入、哪些还缺。

它是一份**对照与缺口清单**，不是 source registry：源记录在
[source-registry.csv](source-registry.csv)，每个 family 的用法见
`architecture/data-acquisition-upgrade.md`。

**先说结论：A 股侧本来就更厚。** 当前 52 个 fetch family 里，中国源覆盖
`cninfo / sse / szse / 上证e互动 / csindex / chinamoney / nbs / eastmoney / hithink / shfe / czce / cffex`，
美国源只有 `sec / fred / bea / treasury / cftc / finra / cboe / bls / yahoo`。所以本地化的重点
**不是补数量，而是补齐三个真正的空档**（国债收益率曲线、分红除权、股东质押 —— **均已补齐**），
并把美股专属通道明确标成"不适用"而不是留着当缺口。

## 1. 逐类对照

| 类别 | 美国通道（已实现） | A 股等价物 | 状态 |
| --- | --- | --- | --- |
| 原始披露（年报/公告） | `sec_companyfacts`, `sec_filings` (L2) | `cninfo_announcements`, `exchange_announcements` (L1) | ✅ 已接，且 A 股是 **L1** 而 SEC 是 L2 |
| 财务报表 | `company_financials` (SEC companyfacts) | `hithink_company_financials` (L5) | ✅ 已接 |
| 定期报告内文表格 | — | `shareholder_count`, `lockup_change` (L1，从报告正文抽) | ✅ A 股独有 |
| 董监高持股 | SEC insider (Form 4) | `executive_holdings` 董监高持股变动 (L5) | ✅ 已接 |
| 十大股东 | SEC 13F/13D | `shareholders_top10`, `shareholders_free_float` (L5) | ✅ 已接 |
| 限售解禁 | — | `lockup_schedule` (L5) | ✅ A 股独有 |
| 投资者关系 | 8-K / earnings call | `ir_activity` 调研记录 (L1), `investor_qa` 互动易/e互动 (L2) | ✅ 已接，A 股更结构化 |
| 指数（点位/成分/估值） | yahoo（仅价格, L5） | `index_benchmark`, `index_members`, `index_valuation` (L2, 中证官方) | ✅ **A 股更强** |
| 指数估值历史 | ❌ 无 | `index_valuation` (L2) | ✅ A 股独有 |
| 个股估值 | yahoo 快照 (L5) | `valuation_snapshot` (L5) | ✅ 已接；**历史估值仍缺** |
| 卖方一致预期 | ❌ 需授权 | `consensus_estimate` (L5, 东财) | ✅ A 股有免费通道 |
| 预期修正历史 | ❌ 需授权 | ❌ | ⚠️ 两边都缺 |
| 宏观序列 | `macro_fred`, `macro_bea`, `macro_series` (BLS) | `macro_nbs`, `macro_china`, `macro_region`, `macro_rates` | ✅ 已接，且 A 股有**分省/地级市** |
| 基准利率 | FRED (DFF/DGS10) | `macro_rates` LPR/Shibor (L2) | ✅ 已接 |
| **国债收益率曲线** | `treasury_debt`, `treasury_avg_interest` (L2) | ❌ **未接** | ❌ **缺口** |
| 期货持仓排名 | `cftc_cot` (L2) | `futures_member_rank` SHFE/CZCE/CFFEX (L2) | ✅ 已接（DCE/GFEX 不可达） |
| 期货仓单 | — | `futures_warehouse` (L5) | ✅ A 股独有 |
| 期货基差 | — | `futures_basis` (L5) | ✅ A 股独有 |
| 期权/波动率 | `cboe_volatility` (19 series), `cboe_implied_vol` (L2) | `option_surface` (L5, 50ETF/300ETF 期权) | ⚠️ 仅 L5；**中国波指(iVX)官方已停止发布** |
| **做空/空头持仓** | `short_interest` FINRA (L2) | ❌ 无等价（A 股个股做空受限）→ 用 `margin_balance` 覆盖近似含义 | ➖ 不适用 |
| 两融 | FINRA margin statistics | `margin_balance`, `margin_market` (L2) | ✅ 已接 |
| 北向资金 | — | `northbound_turnover` (L2 成交额)；**净流入上游已停止披露** | ⚠️ 部分 |
| 龙虎榜/席位 | — | `dragon_tiger` (L5) | ✅ A 股独有 |
| **大宗交易** | — | `block_trade` (L5) | ✅ 刚接入 |
| 行情 | `market_price` yahoo (L5) | `market_price` + `hithink_market_price` + `futu_*` | ✅ 已接 |
| 资金流/持仓 | IBKR / Futu 本地网关 | 同（`ibkr_*`, `futu_*`） | ➖ 与市场无关 |
| 组合持仓 | `ibkr_positions` | 同 | ⚠️ `private/portfolio/` 为空，需用户提供 |

## 2. 三个真正的 A 股空档

### 2a. 国债收益率曲线 ✅ 已接入（2026-10-04）

`cgb_yield_curve`（`chinamoney_rates_api`，L2）。这是美股 `treasury_*` 的 CN 对应物：主权贴现曲线，
也是 A 股风险溢价（尤其股权风险溢价）的定价基准。实测 2026-09-30 曲线 **50 个期限点**，
0.083 年 1.0150% → 46 年，其中 **10Y 1.6830%、30Y 2.0990%**；窗口内共 972 行。

**契约是靠读页面真实请求破解的，不是猜出来的——三处每一处都表现为"源里没有数据"：**

1. **`bondType` 必须是 `CYCC000`。** 我读到的那份资料把 国债 标为 `CYCC001`，而它和邻近代码
   都返回空结果。
2. **`reference=1,2,3` 与 `termId` 是必填的。** 缺了它们响应是 `records: []` 且
   `data: None`——除非你知道内情，否则这和"没有数据"无法区分。
3. **`pageSize` 上限在 50 与 200 之间**：`1000`/`500`/`200` 一律 **HTTP 403**，所以"一次要全部"
   是被拒绝而不是被截断。`pageSize=50` 可用，而且恰好第 1 页就是一整条曲线（50 行）。

窗口**上限一个月**，超出会返回官方提示 `只提供一个月历史数据查询`；adapter 把它作为带原因的
gap 报出，而不是当成空序列。默认只对 12 个基准期限出 claim（全 50 点会让曲线形状淹没在噪声里），
**完整曲线始终进 series**。

### 2b. 分红 / 除权除息 ✅ 已接入（2026-10-04）

`dividend`（`eastmoney_dividend_api`，L5）。`RPT_SHAREBONUS_DET` 实测 56,976 行，给出
分红方案、每股派息、股息率、股权登记日、除权除息日、送转比例，以及 vendor 自己的
除权前后 D10/BD10 漂移。

**三个"比率"字段里只有一个是收益率**，这是本数据集最容易读错的地方：
`DIVIDENT_RATIO` 是股息率且为**比率**（0.010112 = 1.01%，adapter 已换算成百分比），
而 `BONUS_IT_RATIO` 是每 10 股送转股数、`IT_RATIO` 是每 10 股转增股数——都不是百分比，
且只派现时**为 null**，把它们当收益率会凭空造出一次从未宣告的送转。
另外 `PRETAX_BONUS_RMB` 是**每股**，而 `IMPL_PLAN_PROFILE` 写的是每 10 股
（"10派1.80元" 对应字段 1.8），两者一起记入 provenance 以便核对；
`EX_DIVIDEND_DAYS` 是**带符号天数偏移不是日期**（读数为 -18 表示除权日尚未到）。

### 2c. 股东质押 ✅ 已接入（2026-10-04）

`share_pledge`（`eastmoney_share_pledge_api`，L5 转发中国结算 CSDC 数据）。三个视图，均实测：

- **market**：`RPT_CSDC_STATISTICS` —— 全市场质押股份、质押市值、质押公司数、同期沪深300、每日笔数。
  2026-09-30 读数为 **28,064,095.49 万股 / 2,211 家**。
- **stock**：`RPT_CSDC_LIST` —— 个股 **质押比例**、购回余额（无限售/有限售拆分）、笔数、质押市值、行业。
  1,639,892 行。
- **institution**：`RPT_GDZY_ZYJG_SUM` —— 按质权人（券商）汇总，含三个**预警状态**分档与笔数，83 家。

**新鲜度陷阱（这是本通道最要紧的一点）**：停止质押的公司**会直接从名单里消失**，所以它最新的一行是
**最后一次观测**而非当前值——万科A 最新一行是 **2024-04-30**，而 600519/000001/002415/300750 都
是 2026-09-30。把陈旧行当当前值，等于把两年前的质押比例报成今天的；而把"不在名单里"当成 0，
则等于声称该公司无质押。所以记录里带 `lastObservedDate` 与 `stalenessDays`，claim 文本也写明所属日期。

单位统一为 **万股/万元**（与其他 A 股转发一致，**不是** block_trade detail 那种元/股）。
三个预警分档是 vendor 自己的口径，**只报计数、不断言其定义**。

## 3. 明确"不适用"的美股通道

本地化时这几条**不应该**被当成缺口去补，否则会做出没有意义的适配器：

- `short_interest`（FINRA）：A 股个股做空受限，没有等价披露。**近义含义由 `margin_balance`
  覆盖**（融资融券余额），不要把两者混为一谈。
- `macro_fred` / `macro_bea` / `macro_series`(BLS) / `treasury_*` / `cftc_cot` / `cboe_*`：
  均为美国官方统计与市场，A 股研究**不以此为结论依据**。它们保留是因为
  `macro_series` 类目仍是通用容器，且跨市场对比偶尔需要；但**默认工作流应走 CN 通道**。
- `market_price`(yahoo)：A 股有 `hithink_market_price` 与 `futu_*`，yahoo 仅作交叉校验。

## 4. 层级差异（本地化要点）

- **A 股原始披露是 L1**（`cninfo` / `sse` / `szse`），而 SEC 在公司事实接口上是 **L2**。
  同一份年报，A 股读到的层级更高，别沿用美股结论的层级标注。
- **交易所直连优先于聚合**：`exchange_announcements`(L1) > `cninfo_announcements`(L1) >
  `eastmoney`/`hithink` 转发 (L5)。
- **口径**：A 股金额单位常见 万元/亿元，且**同一份数据的不同视图可能用不同单位**——
  `block_trade` 的 detail 是元/股，stats 是万元/万股，已实测确认。凡涉及单位的地方都要用
  算术自证（`价 × 量 = 额`），不要相信字段名。
- **涨跌幅/溢价率**：多为**比率非百分比**（`block_trade` 的 `PREMIUM_RATIO` 实测如此），
  直接当百分比用会差两个数量级。

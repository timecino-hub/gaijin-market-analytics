import Link from "next/link";
import type { ReactNode } from "react";
import { formatDecimalPercent } from "../../lib/analysis-display";
import { getStrategyEvidence, toDisplayError } from "../../lib/api-client";
import { formatDecimal } from "../../lib/formatters";
import {
  daysToHitLabel,
  eventStateLabel,
  exitRuleLabel,
  findHorizon,
  horizonLabel,
  horizonStateLabel,
  medianRisesWithHolding,
  sampleLabel,
  sharePercent,
  signedPercent
} from "../../lib/strategy-evidence-display";
import type {
  ApiError,
  EventLedgerRow,
  ExitRule,
  HorizonEvidence,
  ReturnSummary,
  StrategyEvidenceResponse
} from "../../lib/types";
import { LocalDateTime } from "../local-date-time";
import { MarketHeader } from "../market-header";

export const dynamic = "force-dynamic";

export default async function StrategyPage() {
  const result = await loadEvidence();

  return (
    <main className="art-shell deco-strategy-page">
      <MarketHeader active="strategy" />
      <header className="deco-page-title deco-opportunity-title">
        <div>
          <p className="deco-kicker">GENERAL STRATEGY V1.0.0</p>
          <h1>通用策略</h1>
          <p>一条规则用在每一台载具上，下面是它在目录里每一次已结束活动上的结果。</p>
        </div>
        <div className="deco-opportunity-policy" aria-label="口径">
          <span>只读研究</span>
          <strong>历史台账</strong>
        </div>
      </header>

      {"error" in result ? (
        <ErrorPanel error={result.error} />
      ) : (
        <StrategyResults data={result.data} />
      )}
    </main>
  );
}

function StrategyResults({ data }: { data: StrategyEvidenceResponse }) {
  return (
    <>
      <ScopeSection data={data} />
      <StrategySteps data={data} />
      <HoldingSection data={data} />
      <ExitSection data={data} />
      <LedgerSection data={data} />
    </>
  );
}

function ScopeSection({ data }: { data: StrategyEvidenceResponse }) {
  return (
    <section className="deco-opportunity-summary" aria-labelledby="strategy-summary-heading">
      <div className="deco-section-heading">
        <div>
          <h2 id="strategy-summary-heading">台账范围</h2>
        </div>
        <span>
          {data.selected_event_count} 次入选 / {data.evaluated_event_count} 次可读
        </span>
      </div>

      <div className="deco-opportunity-facts">
        <Info label="实际 as_of" value={<LocalDateTime value={data.as_of} />} />
        <Info label="已结束的活动" value={`${data.completed_event_count} 次`} />
        <Info label="涉及载具" value={`${data.vehicle_count} 台`} />
        <Info
          label="入选门槛"
          value={`入场价 ≤ 基准 × ${formatDecimal(data.drawdown_threshold)}`}
        />
        <Info
          label="入选 / 未入选"
          value={`${data.selected_event_count} / ${data.rejected_event_count}`}
        />
        <Info label="主持有期" value={horizonLabel(data.primary_horizon_days)} />
        <Info label="手续费" value={formatDecimalPercent(data.fee_rate, 2)} />
        <Info
          label="无基准 / 无成交 / 未绑定"
          value={`${data.no_baseline_count} / ${data.no_entry_price_count} / ${data.unmatched_item_count}`}
        />
      </div>
    </section>
  );
}

function StrategySteps({ data }: { data: StrategyEvidenceResponse }) {
  const published = data.exit_frontier.find(
    (rule) => rule.kind === "SPLIT_HALF" && rule.target_net_roi === "0.25"
  );

  return (
    <section className="deco-strategy-steps-block" aria-labelledby="strategy-steps-heading">
      <div className="deco-section-heading">
        <div>
          <h2 id="strategy-steps-heading">四个步骤</h2>
        </div>
        <span>01–03 本页现算 · 04 离线回放 2026-09-21</span>
      </div>

      <ol className="deco-strategy-steps">
        <Step
          index="01"
          title="筛选"
          rule={`活动结束后第 1 个完整成交日的价格 ≤ 活动前基准 × ${formatDecimal(
            data.drawdown_threshold
          )}。`}
          basis={`${data.evaluated_event_count} 次可读活动中 ${data.selected_event_count} 次达到门槛。`}
        />
        <Step
          index="02"
          title="入场"
          rule="活动结束后第 1 个完整成交日，按市价买入。"
          basis="离线回放 2026-09-21：所有择时规则在同等条件下均未胜出。"
        />
        <Step
          index="03"
          title="卖出"
          rule={`一半挂单于手续费后净 +25%，一半持有满 ${data.primary_horizon_days} 天按市价卖出。`}
          basis={
            published === undefined
              ? "已结算活动不足，本次无读数。"
              : `中位 ${signedPercent(published.summary.median)} · ${sharePercent(
                  published.summary.support_ratio
                )} 为正 · 最差十分之一 ${signedPercent(
                  published.summary.tenth_percentile
                )} · ${sampleLabel(published.summary)}`
          }
        />
        <Step
          index="04"
          title="仓位"
          rule="每次等额 5%，同时持有不超过 20 个仓位。"
          basis="离线回放 2026-09-21：上限 10 → 20，年化 7.06% → 9.22%。本页不重算。"
          dated
        />
      </ol>
    </section>
  );
}

function HoldingSection({ data }: { data: StrategyEvidenceResponse }) {
  const primary = findHorizon(data.horizons, data.primary_horizon_days);
  const rises = medianRisesWithHolding(data.horizons);

  return (
    <section aria-labelledby="strategy-holding-heading">
      <div className="deco-section-heading">
        <div>
          <h2 id="strategy-holding-heading">持有期</h2>
        </div>
        <span>
          {rises ? "中位收益随持有期单调上升" : "中位收益未随持有期单调上升"} · 扣费后净收益
        </span>
      </div>

      <div className="deco-strategy-table" role="table" aria-label="不同持有期的收益分布">
        <div className="deco-strategy-head deco-strategy-row-6" role="row">
          <span role="columnheader">持有期</span>
          <span role="columnheader">分组</span>
          <span role="columnheader">样本</span>
          <span role="columnheader">中位</span>
          <span role="columnheader">为正</span>
          <span role="columnheader">最差十分之一</span>
        </div>
        {data.horizons.map((horizon) => (
          <HorizonRows key={horizon.horizon_days} horizon={horizon} />
        ))}
      </div>

      <FootnoteRow
        items={[
          primary === null || primary.pending_count === 0
            ? null
            : `${primary.pending_count} 次活动的 ${primary.horizon_days} 天持有期未走完，不计入`,
          primary === null || primary.superseded_count === 0
            ? null
            : `${primary.superseded_count} 次窗口被下一次投放打断，不计入`,
          primary === null
            ? null
            : `${primary.all_events.sample_size} 次已结算活动来自 ${primary.all_events.vehicle_count} 台载具，样本不独立`,
          "价格为逐日成交均价，非可成交报价"
        ]}
      />
    </section>
  );
}

function HorizonRows({ horizon }: { horizon: HorizonEvidence }) {
  const groups: Array<{ label: string; summary: ReturnSummary; emphasis: boolean }> = [
    { label: "全部", summary: horizon.all_events, emphasis: false },
    { label: "达到门槛", summary: horizon.selected, emphasis: true },
    { label: "未达门槛", summary: horizon.rejected, emphasis: false }
  ];

  return (
    <>
      {groups.map((group, index) => (
        <div
          key={group.label}
          className={`deco-strategy-row deco-strategy-row-6${
            group.emphasis ? " emphasis" : ""
          }${index === 0 ? " group-start" : ""}`}
          role="row"
        >
          <span role="cell">{index === 0 ? horizonLabel(horizon.horizon_days) : ""}</span>
          <span role="cell">{group.label}</span>
          <span role="cell" className="muted">
            {sampleLabel(group.summary)}
          </span>
          <span role="cell">{signedPercent(group.summary.median)}</span>
          <span role="cell">{sharePercent(group.summary.support_ratio)}</span>
          <span role="cell">{signedPercent(group.summary.tenth_percentile)}</span>
        </div>
      ))}
    </>
  );
}

function ExitSection({ data }: { data: StrategyEvidenceResponse }) {
  const settled = data.exit_frontier[0]?.summary ?? null;
  const empty = settled === null || settled.sample_size === 0;

  return (
    <section aria-labelledby="strategy-exit-heading">
      <div className="deco-section-heading">
        <div>
          <h2 id="strategy-exit-heading">卖出方式</h2>
        </div>
        <span>
          达到门槛且 {data.primary_horizon_days} 天已走完 ·{" "}
          {settled === null ? "无样本" : sampleLabel(settled)}
        </span>
      </div>

      {empty ? (
        <div className="deco-opportunity-empty">
          <span aria-hidden="true">00</span>
          <div>
            <h3>暂无已结算的入选活动</h3>
            <p>
              {data.selected_event_count} 次活动达到门槛，均未走完{" "}
              {data.primary_horizon_days} 天持有期。
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="deco-strategy-table" role="table" aria-label="不同卖出方式的结果">
            <div className="deco-strategy-head deco-strategy-row-7" role="row">
              <span role="columnheader">卖出方式</span>
              <span role="columnheader">成交比例</span>
              <span role="columnheader">成交中位天数</span>
              <span role="columnheader">中位</span>
              <span role="columnheader">均值</span>
              <span role="columnheader">为正</span>
              <span role="columnheader">最差十分之一</span>
            </div>
            {data.exit_frontier.map((rule) => (
              <ExitRow key={`${rule.kind}-${rule.target_net_roi ?? "hold"}`} rule={rule} />
            ))}
          </div>

          <FootnoteRow
            items={[
              "净 +25% 的挂单价 = 成本 × 1.25 ÷ 0.85，即涨 47.1%",
              "「成交」= 当日成交均价达到挂单价",
              "未成交的仓位到期按市价卖出，结果计入同一行"
            ]}
          />
        </>
      )}
    </section>
  );
}

function ExitRow({ rule }: { rule: ExitRule }) {
  const published = rule.kind === "SPLIT_HALF" && rule.target_net_roi === "0.25";

  return (
    <div
      className={`deco-strategy-row deco-strategy-row-7${published ? " published" : ""}`}
      role="row"
    >
      <span role="cell">
        {exitRuleLabel(rule)}
        {published ? <b className="deco-strategy-tag">已发布</b> : null}
      </span>
      <span role="cell">{sharePercent(rule.hit_ratio)}</span>
      <span role="cell">{daysToHitLabel(rule.median_days_to_hit)}</span>
      <span role="cell">{signedPercent(rule.summary.median)}</span>
      <span role="cell">{signedPercent(rule.summary.mean)}</span>
      <span role="cell">{sharePercent(rule.summary.support_ratio)}</span>
      <span role="cell">{signedPercent(rule.summary.tenth_percentile)}</span>
    </div>
  );
}

function LedgerSection({ data }: { data: StrategyEvidenceResponse }) {
  return (
    <section aria-labelledby="strategy-ledger-heading">
      <div className="deco-section-heading deco-opportunity-list-heading">
        <div>
          <h2 id="strategy-ledger-heading">逐次活动</h2>
        </div>
        <span>{data.events.length} 次活动 · 按结束时间倒序</span>
      </div>

      {data.events.length === 0 ? (
        <div className="deco-opportunity-empty">
          <span aria-hidden="true">00</span>
          <div>
            <h3>活动目录里没有活动</h3>
            <p>需要载具绑定活动目录，且活动前后都有成交历史。</p>
            <Link href="/items">查看载具目录</Link>
          </div>
        </div>
      ) : (
        <div className="deco-strategy-table" role="table" aria-label="逐次活动的读数">
          <div className="deco-strategy-head deco-strategy-ledger-row" role="row">
            <span role="columnheader">载具</span>
            <span role="columnheader">活动结束</span>
            <span role="columnheader">基准</span>
            <span role="columnheader">入场价</span>
            <span role="columnheader">入场 ÷ 基准</span>
            <span role="columnheader">入选</span>
            <span role="columnheader">{horizonLabel(data.primary_horizon_days)}净收益</span>
          </div>
          {data.events.map((row) => (
            <LedgerRow key={`${row.item_id}-${row.event_key}`} row={row} />
          ))}
        </div>
      )}
    </section>
  );
}

function LedgerRow({ row }: { row: EventLedgerRow }) {
  const settled = row.horizon_state === "SETTLED";

  return (
    <div
      className={`deco-strategy-row deco-strategy-ledger-row${row.selected ? " emphasis" : ""}`}
      role="row"
    >
      <span role="cell">
        <Link href={`/items/${row.item_id}`}>{row.item_name}</Link>
      </span>
      <span role="cell" className="muted">
        <LocalDateTime value={row.closes_at} />
      </span>
      <span role="cell">{currency(row.pre_event_baseline)}</span>
      <span role="cell">{currency(row.entry_price)}</span>
      <span role="cell">{sharePercent(row.drawdown_ratio)}</span>
      <span role="cell">
        {row.state === "EVALUATED" ? (row.selected ? "是" : "否") : eventStateLabel(row.state)}
      </span>
      <span role="cell">
        {settled ? signedPercent(row.net_roi) : horizonStateLabel(row.horizon_state)}
      </span>
    </div>
  );
}

function Step({
  index,
  title,
  rule,
  basis,
  dated = false
}: {
  index: string;
  title: string;
  rule: string;
  basis: string;
  dated?: boolean;
}) {
  return (
    <li className={`deco-strategy-step${dated ? " dated" : ""}`}>
      <span aria-hidden="true">{index}</span>
      <div>
        <h3>{title}</h3>
        <p>{rule}</p>
        <p className="deco-strategy-basis">{basis}</p>
      </div>
    </li>
  );
}

/**
 * The qualifications a number cannot be read without, as a row of short labels.
 *
 * These stay on the page because a median without its sample size, or without
 * the count of events excluded from it, is a different claim from the one the
 * data supports. A null entry is a qualification that does not apply to this
 * reading and is dropped rather than printed as a zero.
 */
function FootnoteRow({ items }: { items: Array<string | null> }) {
  const shown = items.filter((value): value is string => value !== null);
  if (shown.length === 0) {
    return null;
  }
  return (
    <ul className="deco-strategy-footnotes">
      {shown.map((value) => (
        <li key={value}>{value}</li>
      ))}
    </ul>
  );
}

function currency(value: string | null): string {
  return value === null ? "—" : `${formatDecimal(value)} GJN`;
}

function Info({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="deco-info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ErrorPanel({ error }: { error: ApiError }) {
  return (
    <section className="deco-state deco-state-error" aria-live="polite">
      <span aria-hidden="true">!</span>
      <div>
        <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法读取策略台账"}</h2>
        <p>{error.message}</p>
      </div>
    </section>
  );
}

async function loadEvidence(): Promise<
  { data: StrategyEvidenceResponse } | { error: ApiError }
> {
  try {
    return { data: await getStrategyEvidence({}) };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

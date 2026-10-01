import type { ReactNode } from "react";
import { formatCurrencyDisplay, formatDecimalPercent } from "../../lib/analysis-display";
import { generalEntryStatusLabel, generalExitStatusLabel } from "../../lib/general-strategy-display";
import type { ApiError, GeneralStrategyQuery, GeneralStrategyResponse } from "../../lib/types";
import { LocalDateTime } from "../local-date-time";

export function GeneralRulePanel({
  result,
  query,
  budget,
  asOf
}: {
  result: { data: GeneralStrategyResponse } | { error: ApiError };
  query: GeneralStrategyQuery;
  budget: string;
  asOf?: string;
}) {
  return (
    <>
      {"error" in result ? (
        <section className="deco-state deco-state-error" aria-live="polite">
          <span aria-hidden="true">!</span>
          <div>
            <h2>当前通用策略暂不可用</h2>
            <p>{result.error.message}</p>
          </div>
        </section>
      ) : (
        <RuleSteps data={result.data} />
      )}

      <section aria-labelledby="general-calculator-heading">
        <div className="deco-section-heading">
          <h2 id="general-calculator-heading">只读计算器</h2>
        </div>
        <form className="deco-opportunity-filter" action="/strategy" method="get">
          <input type="hidden" name="budget" value={budget} />
          {asOf ? <input type="hidden" name="as_of" value={asOf} /> : null}
          <DecimalField name="baseline" label="活动前参考价（GJN）" value={query.baseline} />
          <DecimalField name="observation_price" label="观察日平均成交价（GJN）" value={query.observation_price} />
          <DecimalField name="quoted_cost" label="次日买入总价（GJN）" value={query.quoted_cost} />
          <label htmlFor="general-event-closes-on">
            活动结束日期（UTC）
            <input id="general-event-closes-on" name="event_closes_on" type="date" defaultValue={query.event_closes_on ?? ""} />
          </label>
          <DecimalField name="entry_cost" label="实际买入花费（GJN）" value={query.entry_cost} />
          <label htmlFor="general-bought-at">
            实际买入时间（含时区）
            <input id="general-bought-at" name="bought_at" type="text" defaultValue={query.bought_at ?? ""} placeholder="2026-10-01T12:00:00Z" />
          </label>
          <DecimalField name="fee_rate" label="手续费率（默认0.15，假设）" value={query.fee_rate} />
          <div className="deco-opportunity-actions"><button type="submit">计算买卖价格</button></div>
        </form>
        {"data" in result ? <CalculatedPlan data={result.data} /> : null}
      </section>
    </>
  );
}

function RuleSteps({ data }: { data: GeneralStrategyResponse }) {
  const { policy } = data;
  return (
    <section className="deco-strategy-steps-block" aria-labelledby="general-rule-heading">
      <div className="deco-section-heading">
        <h2 id="general-rule-heading">当前通用策略</h2>
        <span>买入上限：{formatDecimalPercent(policy.buy_cost_cap)} · 版本：{data.version} · 只读</span>
      </div>
      <ol className="deco-strategy-steps">
        <RuleStep index="01" title="观察" rule={`活动结束前${policy.observation_lead_days}天，用当天的平均成交价与活动前参考价比较；当天结束后，价格不超过参考价的${formatDecimalPercent(policy.observation_gate)}时，次日检查买入价格。`} />
        <RuleStep index="02" title="买入" rule={`买入总价不超过活动前参考价的${formatDecimalPercent(policy.buy_cost_cap)}才买，超过就跳过。`} />
        <RuleStep index="03" title="卖出" rule={`先按扣费后赚${formatDecimalPercent(policy.preferred_net_target)}的价格挂卖；买入满${policy.lower_target_after_days}天还没卖掉，改为扣费后赚${formatDecimalPercent(policy.minimum_net_target)}的价格。`} />
        <RuleStep index="04" title="复核" rule={`买入满${policy.review_after_days}天还没达到目标，重新选择退出时机。`} />
      </ol>
      <ul className="deco-strategy-footnotes">
        <li>默认手续费：15%（假设）</li>
        <li>成交状态：未确认</li>
        <li>{formatDecimalPercent(policy.alternative_cost_cap)}：研究备选</li>
        <li>参考价：活动前同等天数的日均价中位数，至少{policy.minimum_baseline_days}天</li>
      </ul>
    </section>
  );
}

function CalculatedPlan({ data }: { data: GeneralStrategyResponse }) {
  return (
    <>
      <div className="deco-section-heading">
        <h3>买入检查</h3>
        <span>{generalEntryStatusLabel(data.entry.status)} · 评估时间：<LocalDateTime value={data.as_of} /></span>
      </div>
      <div className="deco-opportunity-facts">
        <Fact label="观察日期（UTC）" value={data.entry.observation_on ?? "—"} />
        <Fact label="检查买价日期（UTC）" value={data.entry.quote_check_on ?? "—"} />
        <Fact label="观察价上限（参考价的75%）" value={currency(data.entry.observation_threshold)} />
        <Fact label="买入总价上限（参考价的55%）" value={currency(data.entry.buy_cost_ceiling)} />
        <Fact label="60%研究备选（非默认）" value={currency(data.entry.alternative_buy_cost_ceiling)} />
        <Fact label="活动前参考价" value={currency(data.entry.baseline)} />
      </div>
      <div className="deco-section-heading">
        <h3>卖出计划</h3>
        <span>{generalExitStatusLabel(data.exit.status)}</span>
      </div>
      <div className="deco-opportunity-facts">
        <Fact label="实际买入花费" value={currency(data.exit.entry_cost)} />
        <Fact label="扣费后赚50%的卖价" value={currency(data.exit.preferred_offer)} />
        <Fact label="扣费后赚40%的卖价" value={currency(data.exit.minimum_offer)} />
        <Fact label="当前参考卖价" value={currency(data.exit.planned_offer)} />
        <Fact label="实际买入" value={<LocalDateTime value={data.exit.bought_at} />} />
        <Fact label="150天改挂40%" value={<LocalDateTime value={data.exit.lower_target_at} />} />
        <Fact label="180天重新评估" value={<LocalDateTime value={data.exit.review_at} />} />
        <Fact label="本次手续费率" value={formatDecimalPercent(data.fee_rate)} />
      </div>
      <ul className="deco-strategy-footnotes">
        <li>历史数据截至：<LocalDateTime value={data.research.data_as_of} /></li>
        <li>研究样本：{data.research.opportunity_count}次机会／{data.research.cycle_count}场活动</li>
        <li>挂价：向上取整至0.01 GJN</li>
        <li>净收入：向下取整至0.01 GJN</li>
      </ul>
    </>
  );
}

function DecimalField({ name, label, value }: { name: keyof GeneralStrategyQuery; label: string; value?: string }) {
  return (
    <label htmlFor={`general-${name}`}>
      {label}
      <input id={`general-${name}`} name={name} type="text" inputMode="decimal" defaultValue={value ?? ""} />
    </label>
  );
}

function RuleStep({ index, title, rule }: { index: string; title: string; rule: string }) {
  return <li className="deco-strategy-step"><span aria-hidden="true">{index}</span><div><h3>{title}</h3><p>{rule}</p></div></li>;
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return <div className="deco-info-tile"><span>{label}</span><strong>{value}</strong></div>;
}

function currency(value: string | null): string {
  return value === null ? "—" : `${formatCurrencyDisplay(value)} GJN`;
}

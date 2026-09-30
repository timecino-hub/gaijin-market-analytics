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
            <h2>当前候选规则暂不可用</h2>
            <p>{result.error.message}</p>
          </div>
        </section>
      ) : (
        <RuleSteps data={result.data} />
      )}

      <section aria-labelledby="general-calculator-heading">
        <div className="deco-section-heading">
          <h2 id="general-calculator-heading">只读计算器</h2>
          <span>填写基准与报价，或实际成本与买入时间</span>
        </div>
        <form className="deco-opportunity-filter" action="/strategy" method="get">
          <input type="hidden" name="budget" value={budget} />
          {asOf ? <input type="hidden" name="as_of" value={asOf} /> : null}
          <DecimalField name="baseline" label="活动前基准 B（GJN）" value={query.baseline} />
          <DecimalField name="observation_price" label="完整日VWAP（GJN）" value={query.observation_price} />
          <DecimalField name="quoted_cost" label="次日报价总成本（GJN）" value={query.quoted_cost} />
          <label htmlFor="general-event-closes-on">
            活动结束日期（UTC）
            <input id="general-event-closes-on" name="event_closes_on" type="date" defaultValue={query.event_closes_on ?? ""} />
          </label>
          <DecimalField name="entry_cost" label="实际买入总成本 C（GJN）" value={query.entry_cost} />
          <label htmlFor="general-bought-at">
            实际买入时间（含时区）
            <input id="general-bought-at" name="bought_at" type="text" defaultValue={query.bought_at ?? ""} placeholder="2026-10-01T12:00:00Z" />
            <span className="deco-field-hint">完整时间，例如2026-10-01T12:00:00Z</span>
          </label>
          <DecimalField name="fee_rate" label="卖出手续费率（默认假设0.15）" value={query.fee_rate} />
          <div className="deco-opportunity-actions"><button type="submit">计算门槛与挂价</button></div>
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
        <h2 id="general-rule-heading">当前候选规则</h2>
        <span>默认成本上限 {formatDecimalPercent(policy.buy_cost_cap)} · {data.version} · 只读</span>
      </div>
      <ol className="deco-strategy-steps">
        <RuleStep index="01" title="观察" rule={`活动结束前${policy.observation_lead_days}日的完整日VWAP ≤ B × ${formatDecimalPercent(policy.observation_gate)}。`} basis={`B：活动前等长窗口的完整日VWAP中位数，至少${policy.minimum_baseline_days}个有效日。观察日结束后才能判断。`} />
        <RuleStep index="02" title="买入" rule={`次日核验实际可成交报价；买入总成本 ≤ B × ${formatDecimalPercent(policy.buy_cost_cap)}才满足默认条件。`} basis={`${formatDecimalPercent(policy.alternative_cost_cap)}为另一个研究候选，未设为默认。未满足上限则跳过。`} />
        <RuleStep index="03" title="卖出" rule={`按实际成本C，先争取净${formatDecimalPercent(policy.preferred_net_target)}，成交即可提前退出。`} basis={`第${policy.lower_target_after_days}天仍未成交，改挂最低净${formatDecimalPercent(policy.minimum_net_target)}；以实际卖家净收入核验挂价。`} />
        <RuleStep index="04" title="复核" rule={`第${policy.review_after_days}天仍未达标，另评估合适的后续退出时期。`} basis="保留未退出持仓；不强制清仓，也不将低于净40%的结果标为成功止盈。" />
      </ol>
      <ul className="deco-strategy-footnotes">
        <li>手续费默认15%为研究假设，实际以市场显示的净收入为准</li>
        <li>价格触达不等于成交；候选参数尚未证明通用最优</li>
        <li>仅计算计划，不读取账户或提交订单</li>
      </ul>
    </section>
  );
}

function CalculatedPlan({ data }: { data: GeneralStrategyResponse }) {
  return (
    <>
      <div className="deco-section-heading">
        <h3>买入核验</h3>
        <span>{generalEntryStatusLabel(data.entry.status)} · 本次评估 <LocalDateTime value={data.as_of} /></span>
      </div>
      <div className="deco-opportunity-facts">
        <Fact label="观察日期（UTC）" value={data.entry.observation_on ?? "—"} />
        <Fact label="次日报价核验（UTC）" value={data.entry.quote_check_on ?? "—"} />
        <Fact label="观察价门槛 · B×75%" value={currency(data.entry.observation_threshold)} />
        <Fact label="默认总成本上限 · B×55%" value={currency(data.entry.buy_cost_ceiling)} />
        <Fact label="另一个候选 · B×60%" value={currency(data.entry.alternative_buy_cost_ceiling)} />
        <Fact label="活动前基准 B" value={currency(data.entry.baseline)} />
      </div>
      <div className="deco-section-heading">
        <h3>退出计划</h3>
        <span>{generalExitStatusLabel(data.exit.status)}</span>
      </div>
      <div className="deco-opportunity-facts">
        <Fact label="实际总成本 C" value={currency(data.exit.entry_cost)} />
        <Fact label="净50%目标挂价" value={currency(data.exit.preferred_offer)} />
        <Fact label="净40%最低目标挂价" value={currency(data.exit.minimum_offer)} />
        <Fact label="当前阶段参考挂价" value={currency(data.exit.planned_offer)} />
        <Fact label="实际买入" value={<LocalDateTime value={data.exit.bought_at} />} />
        <Fact label="第150天 · 仍未成交再降目标" value={<LocalDateTime value={data.exit.lower_target_at} />} />
        <Fact label="第180天 · 另评估退出" value={<LocalDateTime value={data.exit.review_at} />} />
        <Fact label="计算采用手续费率" value={formatDecimalPercent(data.fee_rate)} />
      </div>
      <p className="deco-strategy-note">
        挂价按0.01 GJN向上取整，卖家净收入按0.01 GJN向下取整；报价仍须在市场人工核验。
        研究价格数据截至 <LocalDateTime value={data.research.data_as_of} />，
        {data.research.opportunity_count}次机会／{data.research.cycle_count}场活动；不作为当前报价。
      </p>
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

function RuleStep({ index, title, rule, basis }: { index: string; title: string; rule: string; basis: string }) {
  return <li className="deco-strategy-step"><span aria-hidden="true">{index}</span><div><h3>{title}</h3><p>{rule}</p><p className="deco-strategy-basis">{basis}</p></div></li>;
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return <div className="deco-info-tile"><span>{label}</span><strong>{value}</strong></div>;
}

function currency(value: string | null): string {
  return value === null ? "—" : `${formatCurrencyDisplay(value)} GJN`;
}

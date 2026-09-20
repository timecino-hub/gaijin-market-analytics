import Link from "next/link";
import type { ReactNode } from "react";
import {
  analysisReasonLabel,
  formatCurrencyDisplay,
  formatDecimalPercent,
  formatScore
} from "../../lib/analysis-display";
import { getOpportunities, toDisplayError } from "../../lib/api-client";
import { formatBoolean, formatDateTime, formatOptionalText } from "../../lib/formatters";
import {
  opportunityEligibilityLabel,
  opportunityExplanationLabel,
  opportunityLiquidityLabel,
  opportunityQuantityLabel
} from "../../lib/opportunity-display";
import {
  opportunityListStateFromParams,
  opportunityPageQuery
} from "../../lib/opportunity-list-state";
import type {
  ApiError,
  OpportunityRankingItem,
  OpportunityRankingQuery,
  OpportunityRankingResponse
} from "../../lib/types";
import { OpportunityFilterForm } from "./opportunity-filter-form";
import { MarketHeader } from "../market-header";

export const dynamic = "force-dynamic";

type OpportunitiesPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OpportunitiesPage({ searchParams }: OpportunitiesPageProps) {
  const rawParams = await searchParams;
  const state = opportunityListStateFromParams(rawParams);
  const result = await loadOpportunities(state.query);

  return (
    <main className="art-shell deco-opportunity-page">
      <MarketHeader active="opportunities" />
      <header className="deco-page-title deco-opportunity-title">
        <div>
          <p className="deco-kicker">OPPORTUNITY SCORE V1</p>
          <h1>潜力分析</h1>
          <p>
            按统一时间点、周期和 15% 固定费用口径比较真实市场观测。评分用于筛选与解释，
            不代表盈利概率、价格预测或交易建议。
          </p>
        </div>
        <div className="deco-opportunity-policy" aria-label="分析约束">
          <span>只读分析</span>
          <strong>真实观测达到门槛后排名</strong>
        </div>
      </header>
      <OpportunityFilterForm form={state.form} />

      {"error" in result ? (
        <ErrorPanel error={result.error} />
      ) : (
        <OpportunityResults data={result.data} query={state.query} />
      )}
    </main>
  );
}

function OpportunityResults({
  data,
  query
}: {
  data: OpportunityRankingResponse;
  query: OpportunityRankingQuery;
}) {
  const hasPrevious = data.page > 1;
  const hasNext = data.total_pages > 0 && data.page < data.total_pages;

  return (
    <>
      <section className="deco-opportunity-summary" aria-labelledby="opportunity-summary-heading">
        <div className="deco-section-heading">
          <div>
            <h2 id="opportunity-summary-heading">本次排名范围</h2>
            <p>
              在时间窗内评估 {data.evaluated_total} 个商品，其中 {data.eligible_total} 个满足 V1
              资格；筛选后显示 {data.total} 个。
            </p>
          </div>
          <div className="deco-pagination" aria-label="排行榜分页">
            <PageLink disabled={!hasPrevious} page={Math.max(1, data.page - 1)} query={query}>
              上一页
            </PageLink>
            <PageLink disabled={!hasNext} page={data.page + 1} query={query}>
              下一页
            </PageLink>
          </div>
        </div>

        <div className="deco-opportunity-facts">
          <Info label="实际 as_of" value={formatDateTime(data.effective_inputs.as_of)} />
          <Info label="分析周期" value={`${data.effective_inputs.horizon} 天`} />
          <Info
            label="数据新鲜度上限"
            value={`${data.effective_inputs.maximum_snapshot_age_seconds / 3600} 小时`}
          />
          <Info label="最低快照数" value={String(data.effective_inputs.minimum_snapshot_count)} />
          <Info
            label="固定手续费"
            value={formatDecimalPercent(data.effective_inputs.fee_policy.nominal_fee_rate)}
          />
          <Info
            label="算法版本"
            value={`${data.strategy_name} ${data.strategy_version} / ${data.feature_version}`}
          />
          <Info label="当前页" value={`${data.page} / ${Math.max(data.total_pages, 1)}`} />
          <Info label="最低综合分" value={formatScore(data.filters.minimum_score)} />
        </div>
      </section>

      <section aria-labelledby="opportunity-list-heading">
        <div className="deco-section-heading deco-opportunity-list-heading">
          <div>
            <h2 id="opportunity-list-heading">排名结果</h2>
            <p>排序固定为：合格状态、综合分、收益、流动性、新鲜度、可信度和商品 ID。</p>
          </div>
        </div>

        {data.items.length === 0 ? (
          <div className="deco-opportunity-empty">
            <span aria-hidden="true">00</span>
            <div>
              <h3>尚未形成可发布排名</h3>
              <p>
                当前订单簿可以正常浏览，但机会评分至少需要 3 个真实市场快照，且最新快照不能超过 24 小时。
                数据不足时不会复制当前报价、补零或生成示例分数。
              </p>
              <Link href="/items">查看当前市场数据</Link>
            </div>
          </div>
        ) : (
          <div className="opportunity-list">
            {data.items.map((item) => (
              <OpportunityCard key={item.item_id} item={item} horizon={data.effective_inputs.horizon} />
            ))}
          </div>
        )}
      </section>

      <div className="deco-pagination deco-opportunity-bottom-pagination" aria-label="排行榜底部分页">
        <PageLink disabled={!hasPrevious} page={Math.max(1, data.page - 1)} query={query}>
          上一页
        </PageLink>
        <span>
          第 {data.page} 页，共 {Math.max(data.total_pages, 1)} 页
        </span>
        <PageLink disabled={!hasNext} page={data.page + 1} query={query}>
          下一页
        </PageLink>
      </div>
    </>
  );
}

function OpportunityCard({ item, horizon }: { item: OpportunityRankingItem; horizon: number }) {
  return (
    <article className={`deco-opportunity-card ${item.eligible ? "eligible" : "diagnostic"}`}>
      <div className="opportunity-card-heading">
        <div className="rank-block" aria-label={`排名 ${item.rank}`}>
          <span>排名</span>
          <strong>#{item.rank}</strong>
        </div>
        <div className="opportunity-title">
          <div className="opportunity-title-line">
            <h3>
              <Link href={`/items/${item.item_id}`}>{item.item_name}</Link>
            </h3>
            <span className={item.eligible ? "status-badge eligible" : "status-badge diagnostic"}>
              {opportunityEligibilityLabel(item.eligible)}
            </span>
          </div>
          <p>
            {item.external_key} · {item.category} · {formatOptionalText(item.rarity)} ·{" "}
            {formatBoolean(item.is_active)}
          </p>
        </div>
        <div className="score-block">
          <span>综合分</span>
          <strong>{formatScore(item.score)}</strong>
          <small>原始 {formatScore(item.raw_score)}</small>
        </div>
      </div>

      <div className="opportunity-financial-grid">
        <Metric label="当前卖价" value={formatCurrency(item.current_ask)} />
        <Metric label="当前买价" value={formatCurrency(item.current_bid)} />
        <Metric
          label="参考退出价"
          value={formatCurrency(item.reference_sell_price)}
        />
        <Metric label="手续费后净利润" value={formatCurrency(item.net_profit)} />
        <Metric label="手续费后 ROI" value={formatDecimalPercent(item.net_roi)} />
        <Metric
          label="截图数量"
          value={opportunityQuantityLabel(
            item.latest_observed_bid_quantity,
            item.latest_observed_ask_quantity
          )}
        />
      </div>

      <div className="opportunity-components" aria-label="评分构成">
        <ScoreComponent label="收益" value={item.profitability_score} />
        <ScoreComponent label="流动性" value={item.liquidity_score} />
        <ScoreComponent label="稳定性" value={item.stability_score} />
        <ScoreComponent label="数据可信度" value={item.data_confidence_score} />
        <ScoreComponent label="新鲜度" value={item.freshness_score} />
        <ScoreComponent label="风险扣分" value={item.risk_penalty} penalty />
      </div>

      <div className="opportunity-evidence-grid">
        <Info label="流动性来源" value={opportunityLiquidityLabel(item.liquidity_source)} />
        <Info label="数量观察数" value={String(item.quantity_observation_count)} />
        <Info label="价格快照数" value={String(item.observation_count)} />
        <Info label="最新快照" value={formatDateTime(item.last_observation_at)} />
        <Info label="分析状态" value={item.analysis_status} />
        <Info label="详情分析" value={`${horizon} 天周期`} />
      </div>

      <div className="opportunity-explanations">
        <strong>解释与警告</strong>
        <ul className="tag-list">
          {explanationTags(item).map((tag) => (
            <li key={tag.key}>{tag.label}</li>
          ))}
        </ul>
      </div>
    </article>
  );
}

function explanationTags(item: OpportunityRankingItem): Array<{ key: string; label: string }> {
  const tags = [
    ...item.explanation_codes.map((code) => ({
      key: `opportunity:${code}`,
      label: opportunityExplanationLabel(code)
    })),
    ...item.analysis_reason_codes.map((code) => ({
      key: `analysis:${code}`,
      label: analysisReasonLabel(code)
    }))
  ];
  return tags.filter((tag, index) => tags.findIndex((candidate) => candidate.key === tag.key) === index);
}

function formatCurrency(value: string | null): string {
  return value === null ? "—" : `${formatCurrencyDisplay(value)} GJN`;
}

function ScoreComponent({
  label,
  value,
  penalty = false
}: {
  label: string;
  value: string;
  penalty?: boolean;
}) {
  const numeric = Number(value);
  const bounded = Number.isFinite(numeric) ? Math.min(100, Math.max(0, numeric)) : 0;
  return (
    <div className="score-component">
      <div>
        <span>{label}</span>
        <strong>{penalty ? `-${formatScore(value)}` : formatScore(value)}</strong>
      </div>
      <div className="score-track" aria-hidden="true">
        <span style={{ width: `${bounded}%` }} />
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="opportunity-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
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
        <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法生成排行榜"}</h2>
        <p>{error.message}</p>
      </div>
    </section>
  );
}

function PageLink({
  children,
  disabled,
  page,
  query
}: {
  children: ReactNode;
  disabled: boolean;
  page: number;
  query: OpportunityRankingQuery;
}) {
  if (disabled) {
    return (
      <span aria-disabled="true">
        {children}
      </span>
    );
  }
  return (
    <Link
      className="deco-page-link"
      href={{ pathname: "/opportunities", query: opportunityPageQuery(query, page) }}
    >
      {children}
    </Link>
  );
}

async function loadOpportunities(
  query: OpportunityRankingQuery
): Promise<{ data: OpportunityRankingResponse } | { error: ApiError }> {
  try {
    return { data: await getOpportunities(query) };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

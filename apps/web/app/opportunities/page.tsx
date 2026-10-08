import Link from "next/link";
import type { ReactNode } from "react";
import { formatDecimalPercent } from "../../lib/analysis-display";
import { getMarketDataStatus, getVehiclePotential, toDisplayError } from "../../lib/api-client";
import { potentialReferenceQuery } from "../../lib/potential-reference";
import { formatDecimal } from "../../lib/formatters";
import {
  catalogNoteFor,
  eventEvidenceLabel,
  eventOutcomeLabel,
  hasObservedEventOutcome,
  pricePercentileBar,
  potentialSortLabel,
  selectVehiclePotentialItems,
  targetReferenceFor,
  vehicleStateDescription,
  vehicleStateLabel
} from "../../lib/vehicle-potential-display";
import {
  vehiclePotentialStateFromParams,
  type VehiclePotentialListState
} from "../../lib/vehicle-potential-state";
import type {
  ApiError,
  MarketDataStatus,
  VehicleEventRecord,
  VehiclePotentialItem,
  VehiclePotentialResponse
} from "../../lib/types";
import { LocalDateTime } from "../local-date-time";
import { MarketHeader } from "../market-header";
import { VehicleImage, VehicleImageSource } from "../vehicle-image";
import { VehiclePotentialFilterForm } from "./vehicle-potential-filter-form";

export const dynamic = "force-dynamic";

type OpportunitiesPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OpportunitiesPage({ searchParams }: OpportunitiesPageProps) {
  const state = vehiclePotentialStateFromParams(await searchParams);
  const result = await loadPotential(state.query);

  return (
    <main className="art-shell deco-potential-page">
      <MarketHeader active="opportunities" />
      <header className="deco-page-title deco-opportunity-title">
        <div>
          <p className="deco-kicker">VEHICLE POTENTIAL</p>
          <h1>载具潜力</h1>
        </div>
        <div className="deco-opportunity-policy" aria-label="口径">
          <span>只读研究</span>
          <strong>历史比较 · 非收益预测</strong>
        </div>
      </header>

      <VehiclePotentialFilterForm form={state.form} />

      {"error" in result ? (
        <ErrorPanel error={result.error} />
      ) : (
        <PotentialResults data={result.data} form={state.form} status={result.status} />
      )}
    </main>
  );
}

function PotentialResults({
  data, form, status
}: { data: VehiclePotentialResponse; form: VehiclePotentialListState["form"]; status: MarketDataStatus | null }) {
  const items = selectVehiclePotentialItems(data.items, form);
  const version = data.items[0]?.version ?? "—";
  const fee = data.items.map(targetReferenceFor).find((target) => target !== null)?.fee_rate;

  return (
    <>
      <section className="deco-opportunity-summary" aria-labelledby="potential-summary-heading">
        <div className="deco-section-heading">
          <h2 id="potential-summary-heading">载具比较</h2>
          <span>{potentialSortLabel(form.sort)}</span>
        </div>
        <div className="deco-opportunity-facts">
          <Info label="比较时点" value={<LocalDateTime value={data.as_of} />} />
          <Info label="历史数据最新记录日" value={status?.latest_complete_day?.slice(0, 10) ?? "—"} />
          <Info label="数据状态" value={status ? (status.stale ? "数据已过期 · 历史比较" : "近期历史记录") : "状态未读取"} />
          <Info label="历史窗口" value={`${data.history_days} 天`} />
          <Info label="显示载具" value={`${items.length} / ${data.item_count}`} />
          <Info label="计算版本" value={version} />
        </div>
        <p className="deco-potential-action">
          先比较历史价格位置、扣费后的中位价空间和近期成交量，再按
          <Link href="/strategy">通用策略</Link>检查买价。
        </p>
        <ul className="deco-potential-basis" aria-label="计算口径">
          <li>成本参考：最新完整成交日均价</li>
          <li>历史中位价：{data.history_days}天窗口</li>
          <li>手续费：{fee ? formatDecimalPercent(fee, 1) : "—"}</li>
          <li>未计买入价差</li>
          <li>挂价上限：2000 GJN</li>
          <li>最少120个成交日 · 成交价最多14天前</li>
          <li>活动记录不参与排序</li>
        </ul>
      </section>

      <section aria-labelledby="potential-list-heading">
        <div className="deco-section-heading deco-opportunity-list-heading">
          <h2 id="potential-list-heading">逐台比较</h2>
          <span>历史不足、成交价过旧或比较值缺失的排后</span>
        </div>
        {items.length === 0 ? (
          <EmptyPanel />
        ) : (
          <div className="deco-opportunity-list deco-potential-comparison-list">
            {items.map((item, index) => (
              <VehicleCard key={item.item_id} item={item} rank={index + 1} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function VehicleCard({ item, rank }: { item: VehiclePotentialItem; rank: number }) {
  const ranked = item.state === "RANKED";
  const target = targetReferenceFor(item);
  const catalogNote = catalogNoteFor(item);
  const position = ranked && item.history_day_count >= 120 && item.current_price !== null
    ? pricePercentileBar(item.price_percentile) : null;

  return (
    <article className={`deco-opportunity-card deco-potential-card ${ranked ? "ranked" : "unranked"}`}>
      <div className="opportunity-card-heading">
        <div className="rank-block" aria-label={ranked ? "比较顺序" : "未定位"}>
          <span>{ranked ? "顺序" : "未定位"}</span>
          <strong>{ranked ? `#${rank}` : "—"}</strong>
        </div>
        <div className="opportunity-title">
          <div className="deco-potential-identity">
            <Link className="deco-potential-image-link" href={`/items/${item.item_id}`}><VehicleImage externalKey={item.external_key} name={item.item_name} /></Link>
            <div>
              <div className="opportunity-title-line">
                <h3><Link href={`/items/${item.item_id}`}>{item.item_name}</Link></h3>
                <span className={`status-badge ${ranked ? "eligible" : ""}`}>
                  {vehicleStateLabel(item.state)}
                </span>
              </div>
              <p>{item.external_key}{item.category ? ` · ${item.category}` : ""}</p>
            </div>
          </div>
        </div>
        <div className="score-block">
          <span>历史价格分位</span>
          <strong>{formatDecimalPercent(position === null ? null : item.price_percentile, 1)}</strong>
          <small>{item.history_day_count} 个成交日</small>
        </div>
      </div>

      {position === null ? null : (
        <figure
          className="deco-potential-gauge"
          role="img"
          aria-label={`${item.item_name}：最新成交均价处于历史${formatDecimalPercent(item.price_percentile, 1)}分位，依据${item.history_day_count}个成交日`}
        >
          <figcaption>
            <span>历史价格位置</span>
            <strong>最新均价 · {formatDecimalPercent(item.price_percentile, 1)}分位</strong>
          </figcaption>
          <div className="deco-potential-track" aria-hidden="true">
            <span className="deco-potential-fill" style={{ width: `${position}%` }} />
            <i className="deco-potential-median" style={{ left: "50%" }} />
            <b className="deco-potential-marker" style={{ left: `${position}%` }} />
          </div>
          <div className="deco-potential-scale" aria-hidden="true">
            <span>0% · 低位</span>
            <span>50%分位</span>
            <span>100% · 高位</span>
          </div>
        </figure>
      )}

      <div className="opportunity-financial-grid">
        <Metric label="最新成交均价 · 成本参考" value={currency(item.current_price)} />
        <Metric label="历史中位价" value={currency(item.median_price)} />
        <Metric label="到历史中位价净空间" value={formatDecimalPercent(target?.median_net_roi ?? null, 1)} />
        <Metric label="净赚50%所需卖价" value={offer(target?.preferred_offer, target?.preferred_within_market_cap)} />
        <Metric label="净赚40%所需卖价" value={offer(target?.minimum_offer, target?.minimum_within_market_cap)} />
        <Metric label="近30天日成交量中位数" value={volume(item.recent_daily_volume, item.recent_trading_day_count)} />
      </div>

      <div className="deco-potential-card-date">
        <span>最新成交日</span>
        <LocalDateTime value={item.current_price_day} />
      </div>
      {!ranked ? <span className="deco-potential-status">{vehicleStateDescription(item.state)}</span> : null}

      <details className="deco-potential-details">
        <summary>历史与活动记录 · {item.completed_event_count} 次活动 · {item.closed_outcome_count} 次观察期完整</summary>
        <dl className="deco-potential-history">
          <HistoryValue label="窗口最低成交均价" value={currency(item.low_price)} />
          <HistoryValue label="窗口最高成交均价" value={currency(item.high_price)} />
          <HistoryValue label="最新均价 ÷ 历史中位价" value={formatDecimalPercent(item.ratio_to_median, 1)} />
          <HistoryValue label="全期日成交量中位数" value={String(item.median_daily_volume ?? "—")} />
          <HistoryValue label="历史窗口起点" value={<LocalDateTime value={item.window_start} />} />
          <HistoryValue label="完整成交日" value={String(item.history_day_count)} />
          <HistoryValue label="活动中位回撤" value={countedValue(formatDecimalPercent(item.median_drawdown_ratio, 1), item.events.filter((event) => event.drawdown_ratio !== null).length)} />
          <HistoryValue label="旧规则180天中位净空间" value={countedValue(formatDecimalPercent(item.median_net_roi, 1), item.closed_outcome_count)} />
          <HistoryValue label="距上次活动结束" value={item.days_since_last_event_close === null ? "—" : `${item.days_since_last_event_close} 天`} />
        </dl>
        <ul className="deco-potential-basis" aria-label="历史活动口径">
          <li>旧规则买价参考：活动后首个记录日均价</li>
          <li>旧规则退出价：记录日后满180天当天成交均价</li>
          <li>手续费15% · 成交未确认</li>
          <li>{eventEvidenceLabel(item)}</li>
          {catalogNote ? <li>{catalogNote}</li> : null}
        </ul>
        <VehicleImageSource externalKey={item.external_key} />
        {item.events.map((record) => <EventRow key={record.event_key} record={record} />)}
      </details>
    </article>
  );
}

function EventRow({ record }: { record: VehicleEventRecord }) {
  return (
    <section className="deco-potential-event-record" aria-label={record.event_key}>
      <h4>{record.event_key}</h4>
      <dl className="deco-potential-history">
        <HistoryValue label="活动结束时间" value={<LocalDateTime value={record.closes_at} />} />
        <HistoryValue label="活动前参考价" value={currency(record.pre_event_baseline)} />
        <HistoryValue label="活动后首个记录日均价" value={currency(record.entry_price)} />
        <HistoryValue label="买价参考成交日" value={<LocalDateTime value={record.entry_price_day ?? null} />} />
        <HistoryValue label="回撤" value={formatDecimalPercent(record.drawdown_ratio, 1)} />
        <HistoryValue label={`满${record.holding_days}天净空间`} value={hasObservedEventOutcome(record) ? formatDecimalPercent(record.net_roi, 1) : "—"} />
        <HistoryValue label="观察状态" value={eventOutcomeLabel(record)} />
      </dl>
    </section>
  );
}

function EmptyPanel() {
  return (
    <div className="deco-opportunity-empty">
      <span aria-hidden="true">00</span>
      <div>
        <h3>没有符合条件的载具</h3>
        <Link href="/opportunities">重置筛选</Link>
      </div>
    </div>
  );
}

function countedValue(value: string, count: number): string {
  return value === "—" ? "—" : `${value}（n=${count}）`;
}

function currency(value: string | null | undefined): string {
  return value == null ? "—" : `${formatDecimal(value)} GJN`;
}

function offer(value: string | undefined, withinCap: boolean | undefined): ReactNode {
  return <>{currency(value)}{withinCap === false ? <small>超过挂价上限</small> : null}</>;
}

function volume(value: number | null, days: number | null | undefined): ReactNode {
  return <>{value ?? "—"}{days == null ? null : <small>{days} 个成交日 / 30天窗口</small>}</>;
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return <div className="opportunity-metric"><span>{label}</span><strong>{value}</strong></div>;
}

function Info({ label, value }: { label: string; value: ReactNode }) {
  return <div className="deco-info-tile"><span>{label}</span><strong>{value}</strong></div>;
}

function HistoryValue({ label, value }: { label: string; value: ReactNode }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function ErrorPanel({ error }: { error: ApiError }) {
  return (
    <section className="deco-state deco-state-error" aria-live="polite">
      <span aria-hidden="true">!</span>
      <div>
        <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法读取载具潜力"}</h2>
        <p>{error.message}</p>
      </div>
    </section>
  );
}

async function loadPotential(
  query: ReturnType<typeof vehiclePotentialStateFromParams>["query"]
): Promise<{ data: VehiclePotentialResponse; status: MarketDataStatus | null } | { error: ApiError }> {
  try {
    const status = await getMarketDataStatus().catch(() => null);
    return { data: await getVehiclePotential(potentialReferenceQuery(query, status)), status };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

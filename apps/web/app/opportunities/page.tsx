import Link from "next/link";
import type { ReactNode } from "react";
import { formatDecimalPercent } from "../../lib/analysis-display";
import { getVehiclePotential, toDisplayError } from "../../lib/api-client";
import { formatDecimal } from "../../lib/formatters";
import {
  catalogNoteFor,
  eventEvidenceSentence,
  isThinEvidence,
  pricePercentileBar,
  pricePositionSentence,
  vehicleExplanationLabel,
  vehicleStateDescription,
  vehicleStateLabel,
  volumeLabel
} from "../../lib/vehicle-potential-display";
import { vehiclePotentialStateFromParams } from "../../lib/vehicle-potential-state";
import type {
  ApiError,
  VehicleEventRecord,
  VehiclePotentialItem,
  VehiclePotentialResponse
} from "../../lib/types";
import { LocalDateTime } from "../local-date-time";
import { MarketHeader } from "../market-header";
import { VehiclePotentialFilterForm } from "./vehicle-potential-filter-form";

export const dynamic = "force-dynamic";

type OpportunitiesPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OpportunitiesPage({ searchParams }: OpportunitiesPageProps) {
  const rawParams = await searchParams;
  const state = vehiclePotentialStateFromParams(rawParams);
  const result = await loadPotential(state.query);

  return (
    <main className="art-shell deco-potential-page">
      <MarketHeader active="opportunities" />
      <header className="deco-page-title deco-opportunity-title">
        <div>
          <p className="deco-kicker">VEHICLE POTENTIAL V1.0.0</p>
          <h1>载具潜力</h1>
          <p>
            每台载具只用它自己的成交历史来衡量：现价落在它自己过去两年分布的第几分位。
            这里不比较不同载具的好坏，也不预测涨跌——只回答「这台现在相对它自己便宜还是贵」。
          </p>
        </div>
        <div className="deco-opportunity-policy" aria-label="口径">
          <span>只读研究</span>
          <strong>位置描述，不是预测</strong>
        </div>
      </header>

      <VehiclePotentialFilterForm form={state.form} />

      {"error" in result ? (
        <ErrorPanel error={result.error} />
      ) : (
        <PotentialResults data={result.data} />
      )}
    </main>
  );
}

function PotentialResults({ data }: { data: VehiclePotentialResponse }) {
  const version = data.items[0]?.version ?? "—";

  return (
    <>
      <section className="deco-opportunity-summary" aria-labelledby="potential-summary-heading">
        <div className="deco-section-heading">
          <div>
            <h2 id="potential-summary-heading">本次定位范围</h2>
            <p>
              读取 {data.item_count} 台载具，其中 {data.ranked_count} 台的成交历史足够长，
              可以把现价放回自己的分布里。
            </p>
          </div>
          <span>按现价分位由低到高</span>
        </div>

        <div className="deco-opportunity-facts">
          <Info label="实际 as_of" value={<LocalDateTime value={data.as_of} />} />
          <Info label="历史窗口" value={`${data.history_days} 天`} />
          <Info label="可定位载具" value={`${data.ranked_count} / ${data.item_count}`} />
          <Info label="排序依据" value="现价在自身历史中的分位" />
          <Info label="定位所需最少成交日" value="120 天" />
          <Info label="活动样本" value="每台 1–5 次，不用于排序" />
          <Info label="数据来源" value="逐日成交均价与成交量" />
          <Info label="规则版本" value={version} />
        </div>
      </section>

      <MethodNotice />

      <section aria-labelledby="potential-list-heading">
        <div className="deco-section-heading deco-opportunity-list-heading">
          <div>
            <h2 id="potential-list-heading">逐台定位</h2>
            <p>
              排序只用价格分位。历史不足或现价过旧的载具排在最后，不会因为算不出分数而排到前面。
            </p>
          </div>
        </div>

        {data.items.length === 0 ? (
          <EmptyPanel />
        ) : (
          <div className="deco-opportunity-list">
            {data.items.map((item, index) => (
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
  const bar = pricePercentileBar(item.price_percentile);
  const catalogNote = catalogNoteFor(item);

  return (
    <article
      className={`deco-opportunity-card deco-potential-card ${ranked ? "ranked" : "unranked"}`}
    >
      <div className="opportunity-card-heading">
        <div className="rank-block" aria-label={ranked ? "分位排名" : "未定位"}>
          <span>{ranked ? "排名" : "未定位"}</span>
          <strong>{ranked ? `#${rank}` : "—"}</strong>
        </div>
        <div className="opportunity-title">
          <div className="opportunity-title-line">
            <h3>
              <Link href={`/items/${item.item_id}`}>{item.item_name}</Link>
            </h3>
            <span className={`status-badge ${ranked ? "eligible" : ""}`}>
              {vehicleStateLabel(item.state)}
            </span>
          </div>
          <p>
            {item.external_key}
            {item.category ? ` · ${item.category}` : ""}
            {item.matched_catalog_name ? ` · 目录名 ${item.matched_catalog_name}` : ""}
          </p>
        </div>
        <div className="score-block">
          <span>现价分位</span>
          <strong>{formatDecimalPercent(item.price_percentile, 1)}</strong>
          <small>{ranked ? `共 ${item.history_day_count} 个成交日` : "无法定位"}</small>
        </div>
      </div>

      <p className="deco-potential-note">
        {ranked ? pricePositionSentence(item) : vehicleStateDescription(item.state)}
      </p>

      {bar === null ? null : (
        <div className="deco-potential-gauge" aria-hidden="true">
          <b className="deco-potential-marker" style={{ left: `${bar}%` }}>
            现价 {formatDecimalPercent(item.price_percentile, 1)}
          </b>
          <div className="deco-potential-track">
            <span className="deco-potential-fill" style={{ width: `${bar}%` }} />
            <i className="deco-potential-median" style={{ left: "50%" }} />
          </div>
          <div className="deco-potential-scale">
            <span>两年最低</span>
            <span>中位</span>
            <span>两年最高</span>
          </div>
        </div>
      )}

      <div className="deco-potential-block-label">价格位置</div>
      <div className="opportunity-financial-grid">
        <Metric label="现价" value={currency(item.current_price)} />
        <Metric label="自身中位价" value={currency(item.median_price)} />
        <Metric
          label="现价 ÷ 中位"
          value={formatDecimalPercent(item.ratio_to_median, 1)}
        />
        <Metric label="窗口最低" value={currency(item.low_price)} />
        <Metric label="窗口最高" value={currency(item.high_price)} />
        <Metric
          label="日均成交量（全期 / 近 30 天）"
          value={volumeLabel(item.median_daily_volume, item.recent_daily_volume)}
        />
      </div>

      <div className="deco-potential-block-label">
        活动记录
        <em>
          {item.completed_event_count} 次已结束 · {item.closed_outcome_count} 次已结算
          {isThinEvidence(item) ? " · 样本过少" : ""}
        </em>
      </div>
      <p className="deco-potential-note">{eventEvidenceSentence(item)}</p>

      {item.events.length === 0 ? null : (
        <div className="deco-potential-events">
          <div className="deco-potential-events-head" aria-hidden="true">
            <span>活动</span>
            <span>结束时间</span>
            <span>活动前基准</span>
            <span>次日价</span>
            <span>回撤</span>
            <span>持有 {item.events[0].holding_days} 天后</span>
          </div>
          {item.events.map((record) => (
            <EventRow key={record.event_key} record={record} />
          ))}
        </div>
      )}

      <div className="opportunity-evidence-grid">
        <Info label="最新成交日" value={<LocalDateTime value={item.current_price_day} />} />
        <Info
          label="历史窗口起点"
          value={<LocalDateTime value={item.window_start} />}
        />
        <Info label="完整成交日" value={String(item.history_day_count)} />
        <Info
          label="活动中位回撤"
          value={countedValue(
            formatDecimalPercent(item.median_drawdown_ratio, 1),
            item.completed_event_count
          )}
        />
        <Info
          label="活动中位净收益"
          value={countedValue(
            formatDecimalPercent(item.median_net_roi, 1),
            item.closed_outcome_count
          )}
        />
        <Info
          label="距上次活动结束"
          value={
            item.days_since_last_event_close === null
              ? "—"
              : `${item.days_since_last_event_close} 天`
          }
        />
      </div>

      {catalogNote ? <p className="deco-potential-note">{catalogNote}</p> : null}

      {item.explanation_codes.length === 0 ? null : (
        <div className="opportunity-explanations">
          <strong>解释与警告</strong>
          <ul className="tag-list">
            {item.explanation_codes.map((code) => (
              <li key={code}>{vehicleExplanationLabel(code)}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function EventRow({ record }: { record: VehicleEventRecord }) {
  return (
    <div className="deco-potential-event-row">
      <span title={record.event_key}>{record.event_key}</span>
      <span>
        <LocalDateTime value={record.closes_at} />
      </span>
      <span>{currency(record.pre_event_baseline)}</span>
      <span>{currency(record.entry_price)}</span>
      <span>{formatDecimalPercent(record.drawdown_ratio, 1)}</span>
      <span>
        {record.outcome_closed
          ? formatDecimalPercent(record.net_roi, 1)
          : "持有期未走完"}
      </span>
    </div>
  );
}

function MethodNotice() {
  return (
    <section className="deco-postevent-evidence" aria-labelledby="potential-method-heading">
      <div>
        <p className="deco-kicker">METHOD</p>
        <h2 id="potential-method-heading">这页能说明什么</h2>
        <p className="deco-postevent-evidence-aside">
          两块证据的强度差得很远，所以只有其中一块参与排序。
        </p>
      </div>
      <div className="deco-postevent-evidence-body">
        <p>
          <strong>价格位置</strong>用的是每台载具自己的几百到上千个完整成交日，
          现价的分位是一次实打实的测量，排序只用它。
        </p>
        <p>
          <strong>活动记录</strong>是这台载具在自己历次活动中的表现。它有参考价值，
          但每台只有 1 到 5 次活动，按它排名等于按抽样噪声排名，所以它只作为证据展开，
          每一个汇总数字都带着它的样本量。
        </p>
        <p className="deco-postevent-caveat">
          <strong>低分位不等于会涨。</strong>一台载具处在自己历史的底部，可能是因为便宜，
          也可能是因为它还在跌——只看这条价格序列区分不了这两种情况。
          想看「活动结束后买入」那套通用策略，在
          <Link href="/postevent-entry">活动后入场</Link>。
        </p>
      </div>
    </section>
  );
}

function EmptyPanel() {
  return (
    <div className="deco-opportunity-empty">
      <span aria-hidden="true">00</span>
      <div>
        <h3>没有可定位的载具</h3>
        <p>
          当前筛选条件下没有任何商品。定位需要足够长的逐日成交历史，缺少时不会被凭空补齐。
        </p>
        <Link href="/items">查看载具目录</Link>
      </div>
    </div>
  );
}

function countedValue(value: string, count: number): string {
  return value === "—" ? "—" : `${value}（n=${count}）`;
}

// A daily VWAP is an average, so it carries more digits than the 0.01 GJN the
// market actually trades in. Two places is what a reader can act on; the extra
// precision is real but not information.
function currency(value: string | null): string {
  return value === null ? "—" : `${formatDecimal(value)} GJN`;
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="opportunity-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
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
        <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法读取载具潜力"}</h2>
        <p>{error.message}</p>
      </div>
    </section>
  );
}

async function loadPotential(
  query: ReturnType<typeof vehiclePotentialStateFromParams>["query"]
): Promise<{ data: VehiclePotentialResponse } | { error: ApiError }> {
  try {
    return { data: await getVehiclePotential(query) };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

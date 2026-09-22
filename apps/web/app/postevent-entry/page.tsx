import Link from "next/link";
import { formatCurrencyDisplay, formatDecimalPercent } from "../../lib/analysis-display";
import { getPostEventEntry, toDisplayError } from "../../lib/api-client";
import { formatDateTime } from "../../lib/formatters";
import {
  catalogMatchLabel,
  comparePostEventEntry,
  daysSinceCloseLabel,
  drawdownGaugePercent,
  entryCandidateSummary,
  formatTakeProfitPrice,
  postEventEntryTone,
  postEventExplanationLabel,
  postEventStateDescription,
  postEventStateLabel
} from "../../lib/postevent-entry-display";
import { postEventEntryStateFromParams } from "../../lib/postevent-entry-state";
import type { ApiError, PostEventEntryResponse, PostEventEntrySignal } from "../../lib/types";
import { MarketHeader } from "../market-header";
import { PostEventEntryFilterForm } from "./postevent-entry-filter-form";

export const dynamic = "force-dynamic";

type PostEventEntryPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function PostEventEntryPage({ searchParams }: PostEventEntryPageProps) {
  const rawParams = await searchParams;
  const state = postEventEntryStateFromParams(rawParams);
  const result = await loadSignals(state.query);
  const candidatesOnly = state.form.candidatesOnly;

  return (
    <main className="art-shell deco-postevent-page">
      <MarketHeader active="postevent-entry" />
      <header className="deco-page-title deco-opportunity-title">
        <div>
          <p className="deco-kicker">POST-EVENT ENTRY RULE V1.0.0</p>
          <h1>活动后入场</h1>
          <p>
            供给活动结束后，价格通常先跌后回。这条规则只做一件事：在活动结束后的第 1 天，
            检查现价是否跌到活动前基准的 75% 以下。达到就标注为候选，没达到就照实说没达到。
            它标注、不过滤，也不代表成交保证或交易建议。
          </p>
        </div>
        <div className="deco-opportunity-policy" aria-label="策略口径">
          <span>ANNOTATE 档</span>
          <strong>只标注，不隐藏任何载具</strong>
        </div>
      </header>

      <PostEventEntryFilterForm form={state.form} />

      {"error" in result ? (
        <ErrorPanel error={result.error} />
      ) : (
        <PostEventEntryResults data={result.data} candidatesOnly={candidatesOnly} />
      )}
    </main>
  );
}

function PostEventEntryResults({
  data,
  candidatesOnly
}: {
  data: PostEventEntryResponse;
  candidatesOnly: boolean;
}) {
  const items = [...data.items].sort(comparePostEventEntry);
  const matched = items.filter((item) => item.catalog_match_status === "MATCHED");
  const latestWindow = items.find((item) => item.days_since_close !== null);
  const version = items.find((item) => item.version !== null)?.version ?? null;

  return (
    <>
      <section className="deco-opportunity-summary" aria-labelledby="postevent-summary-heading">
        <div className="deco-section-heading">
          <div>
            <h2 id="postevent-summary-heading">本次评估范围</h2>
            <p>
              {entryCandidateSummary(
                data.entry_candidate_count,
                data.item_count,
                candidatesOnly
              )}
            </p>
          </div>
          <span>{matched.length} 台已绑定活动目录</span>
        </div>

        <div className="deco-opportunity-facts">
          <Info label="实际 as_of" value={formatDateTime(data.as_of)} />
          <Info label="策略档位" value={`${data.policy}（标注，不过滤）`} />
          <Info
            label={candidatesOnly ? "入场候选（已筛选）" : "入场候选"}
            value={
              candidatesOnly
                ? String(data.entry_candidate_count)
                : `${data.entry_candidate_count} / ${data.item_count}`
            }
          />
          <Info label="回撤门槛" value="≤ 活动前基准的 75%" />
          <Info label="入场窗口" value="活动结束后 7 天内，以第 1 天为准" />
          <Info label="建议持有" value="180 天" />
          <Info label="止盈口径" value="手续费后净 25%（挂单价 = 成本 × 1.25 ÷ 0.85）" />
          <Info label="规则版本" value={version ?? "—"} />
        </div>
      </section>

      <EvidenceNotice />

      <section aria-labelledby="postevent-list-heading">
        <div className="deco-section-heading deco-opportunity-list-heading">
          <div>
            <h2 id="postevent-list-heading">逐台读数</h2>
            <p>
              排序固定为：入场候选优先，其次按距离活动结束的天数，最后按商品 ID。
              未绑定活动目录的载具照样列出，不会被悄悄省略。
            </p>
          </div>
        </div>

        {items.length === 0 ? (
          <EmptyPanel candidatesOnly={candidatesOnly} />
        ) : (
          <>
            {data.entry_candidate_count === 0 ? (
              <NoCandidateNotice latestWindow={latestWindow ?? null} />
            ) : null}
            <div className="deco-opportunity-list">
              {items.map((item) => (
                <PostEventEntryCard key={item.item_id} item={item} />
              ))}
            </div>
          </>
        )}
      </section>
    </>
  );
}

function PostEventEntryCard({ item }: { item: PostEventEntrySignal }) {
  const tone = postEventEntryTone(item);
  const gauge = drawdownGaugePercent(item.drawdown_ratio);
  const threshold = drawdownGaugePercent(item.drawdown_threshold);

  return (
    <article className={`deco-opportunity-card deco-postevent-card tone-${tone}`}>
      <div className="opportunity-card-heading">
        <div className="rank-block" aria-label="距离活动结束">
          <span>距活动结束</span>
          <strong>{item.days_since_close === null ? "—" : item.days_since_close}</strong>
          <small>{item.days_since_close === null ? "无窗口" : "天"}</small>
        </div>
        <div className="opportunity-title">
          <div className="opportunity-title-line">
            <h3>
              <Link href={`/items/${item.item_id}`}>{item.item_name}</Link>
            </h3>
            <span className={`status-badge ${item.is_entry_candidate ? "eligible" : ""}`}>
              {postEventStateLabel(item.state)}
            </span>
          </div>
          <p>
            {item.external_key} · {catalogMatchLabel(item.catalog_match_status)}
            {item.matched_catalog_name ? ` · 目录名 ${item.matched_catalog_name}` : ""}
          </p>
        </div>
        <div className="score-block">
          <span>现价 ÷ 基准</span>
          <strong>{formatDecimalPercent(item.drawdown_ratio, 2)}</strong>
          <small>
            {item.drawdown_threshold === null
              ? "无门槛读数"
              : `门槛 ≤ ${formatDecimalPercent(item.drawdown_threshold, 2)}`}
          </small>
        </div>
      </div>

      <p className="deco-postevent-state-note">{postEventStateDescription(item.state)}</p>

      {gauge === null ? null : (
        <div className="deco-postevent-gauge" aria-hidden="true">
          {threshold === null ? null : (
            <b className="deco-postevent-threshold-note" style={{ left: `${threshold}%` }}>
              门槛 {threshold.toFixed(0)}%
            </b>
          )}
          <div className="deco-postevent-track">
            <span className="deco-postevent-fill" style={{ width: `${gauge}%` }} />
            {threshold === null ? null : (
              <i className="deco-postevent-threshold" style={{ left: `${threshold}%` }} />
            )}
          </div>
          <div className="deco-postevent-scale">
            <span>0%</span>
            <span>活动前基准 100%</span>
          </div>
        </div>
      )}

      <div className="opportunity-financial-grid">
        <Metric label="活动前基准" value={currency(item.pre_event_baseline)} />
        <Metric label="活动后现价" value={currency(item.latest_price)} />
        <Metric
          label="止盈挂单价（向上取整 0.01）"
          value={
            item.is_entry_candidate && item.take_profit_price !== null
              ? `${formatTakeProfitPrice(item.take_profit_price)} GJN`
              : "—"
          }
        />
        <Metric
          label="止盈净收益"
          value={
            item.is_entry_candidate
              ? formatDecimalPercent(item.take_profit_net_roi, 2)
              : "—"
          }
        />
        <Metric
          label="持有到期"
          value={item.is_entry_candidate ? formatDateTime(item.hold_until) : "—"}
        />
        <Metric label="成交保证" value={item.fill_claim ? "是" : "否，仅为读数"} />
      </div>

      <div className="opportunity-evidence-grid">
        <Info label="活动标识" value={item.event_key ?? "—"} />
        <Info label="活动结束时间" value={formatDateTime(item.event_closed_at)} />
        <Info label="距今" value={daysSinceCloseLabel(item.days_since_close)} />
        <Info label="目录匹配" value={catalogMatchLabel(item.catalog_match_status)} />
        <Info label="用途" value={item.research_only ? "仅研究参考" : "—"} />
        <Info label="规则版本" value={item.version ?? "—"} />
      </div>

      {item.explanation_codes.length === 0 ? null : (
        <div className="opportunity-explanations">
          <strong>解释与警告</strong>
          <ul className="tag-list">
            {item.explanation_codes.map((code) => (
              <li key={code}>{postEventExplanationLabel(code)}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function EvidenceNotice() {
  return (
    <section className="deco-postevent-evidence" aria-labelledby="postevent-evidence-heading">
      <div>
        <p className="deco-kicker">EVIDENCE</p>
        <h2 id="postevent-evidence-heading">证据强度</h2>
        <p className="deco-postevent-evidence-aside">
          描述性统计与严格时点验证给出的结论差距很大，两个数字都列在这里。
        </p>
      </div>
      <div className="deco-postevent-evidence-body">
        <p>
          在 101 个已完成的活动样本上，活动结束后第 1 天买入并持有 180 天的中位收益为
          +8.7%；只保留现价跌到基准 75% 以下的那一半，中位收益为 +25.2%，
          正收益比例 84.0%。门槛从 0.55 到 1.00 是一条平滑单调的曲线，不是挑出来的尖点。
        </p>
        <p className="deco-postevent-caveat">
          但这是描述性统计。改用严格时点回放（只用当时已知的数据选门槛）后，
          正收益比例只从 67.0% 提升到 69.6%，也就是 +2.6 个百分点，而不是描述性口径下的
          +15.6 个百分点。差距来自 180 天的标签结算太慢，可用于决策的历史很少。
          <strong>页面上的读数按前者呈现，可信度请按后者衡量。</strong>
        </p>
      </div>
    </section>
  );
}

function NoCandidateNotice({ latestWindow }: { latestWindow: PostEventEntrySignal | null }) {
  return (
    <div className="deco-opportunity-empty deco-postevent-empty">
      <span aria-hidden="true">00</span>
      <div>
        <h3>当前没有入场候选</h3>
        <p>
          这是规则给出的答案，不是数据缺失。入场窗口只在活动结束后的 7 天内开启，
          而目前所有已绑定载具的窗口都已经关闭
          {latestWindow?.event_closed_at
            ? `——最近一次活动结束于 ${formatDateTime(latestWindow.event_closed_at)}，已过 ${daysSinceCloseLabel(latestWindow.days_since_close)}`
            : ""}
          。下一轮活动结束的第二天，这里才会重新出现候选。
        </p>
        <Link href="/opportunities">查看潜力分析</Link>
      </div>
    </div>
  );
}

function EmptyPanel({ candidatesOnly }: { candidatesOnly: boolean }) {
  if (candidatesOnly) {
    return (
      <div className="deco-opportunity-empty">
        <span aria-hidden="true">00</span>
        <div>
          <h3>当前没有入场候选</h3>
          <p>
            这是规则给出的答案，不是数据缺失。现在只显示入场候选，而没有载具同时处于入场窗口内
            且回撤达到门槛。去掉这个筛选，可以看到每一台载具的读数和它没有入选的原因。
          </p>
          <Link href="/postevent-entry">显示全部载具</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="deco-opportunity-empty">
      <span aria-hidden="true">00</span>
      <div>
        <h3>没有可评估的载具</h3>
        <p>
          当前筛选条件下没有任何商品。这条规则需要商品同时具备活动目录绑定和活动前后的成交历史，
          缺少任何一项都不会被凭空补齐。
        </p>
        <Link href="/items">查看载具目录</Link>
      </div>
    </div>
  );
}

function currency(value: string | null): string {
  return value === null ? "—" : `${formatCurrencyDisplay(value)} GJN`;
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
        <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法读取活动后入场信号"}</h2>
        <p>{error.message}</p>
      </div>
    </section>
  );
}

async function loadSignals(
  query: ReturnType<typeof postEventEntryStateFromParams>["query"]
): Promise<{ data: PostEventEntryResponse } | { error: ApiError }> {
  try {
    return { data: await getPostEventEntry(query) };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

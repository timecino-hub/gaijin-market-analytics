import Link from "next/link";
import { getItem, getItemSnapshots, toDisplayError } from "../../../lib/api-client";
import { formatBoolean, formatDateTime, formatDecimal, formatOptionalText } from "../../../lib/formatters";
import type { ApiError, ItemDetail, MarketSnapshot, SnapshotQuery, SortOrder } from "../../../lib/types";
import { SnapshotFilterForm } from "./snapshot-filter-form";

type ItemDetailPageProps = {
  params: Promise<{ itemId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ItemDetailPage({ params, searchParams }: ItemDetailPageProps) {
  const { itemId } = await params;
  const query = toSnapshotQuery(await searchParams);
  const result = await loadItemDetail(itemId, query);

  if ("error" in result) {
    const displayError = result.error;
    return (
      <main className="page-shell">
        <header className="page-header">
          <Link href="/items" className="back-link">
            返回商品列表
          </Link>
          <h1>{displayError.code === "item_not_found" ? "商品不存在" : "无法加载商品详情"}</h1>
        </header>
        <section className="error-state" aria-live="polite">
          <h2>{displayError.code === "api_unreachable" ? "API 不可访问" : "请求失败"}</h2>
          <p>{displayError.message}</p>
        </section>
      </main>
    );
  }

  const { item, snapshots } = result;

  return (
    <main className="page-shell">
      <header className="page-header">
        <Link href="/items" className="back-link">
          返回商品列表
        </Link>
        <h1>{item.name}</h1>
        <p>收益与风险分析功能将在后续阶段加入。</p>
      </header>

      <section className="detail-grid" aria-label="商品基础信息">
        <Info label="external_key" value={item.external_key} />
        <Info label="分类" value={item.category} />
        <Info label="稀有度" value={formatOptionalText(item.rarity)} />
        <Info label="状态" value={formatBoolean(item.is_active)} />
        <Info label="快照数量" value={String(item.snapshot_count)} />
        <Info label="首次观测" value={formatDateTime(item.first_snapshot_at)} />
        <Info label="最后观测" value={formatDateTime(item.last_snapshot_at)} />
      </section>

      <section className="panel" aria-labelledby="latest-heading">
        <h2 id="latest-heading">最新市场快照</h2>
        {item.latest_snapshot ? (
          <div className="detail-grid compact">
            <Info label="observed_at" value={formatDateTime(item.latest_snapshot.observed_at)} />
            <Info label="best_ask" value={formatDecimal(item.latest_snapshot.best_ask)} />
            <Info label="best_bid" value={formatDecimal(item.latest_snapshot.best_bid)} />
            <Info label="ask_count" value={formatOptionalNumber(item.latest_snapshot.ask_count)} />
            <Info label="bid_count" value={formatOptionalNumber(item.latest_snapshot.bid_count)} />
            <Info
              label="estimated_volume"
              value={formatDecimal(item.latest_snapshot.estimated_volume)}
            />
          </div>
        ) : (
          <div className="empty-state">
            <h3>商品存在但没有快照</h3>
            <p>当前商品尚未导入任何市场快照。</p>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="history-heading">
        <div className="section-heading">
          <div>
            <h2 id="history-heading">历史快照</h2>
            <p>不补零、不插值，只展示后端返回的已观测记录。</p>
          </div>
        </div>
        <SnapshotFilterForm itemId={itemId} />

        {snapshots.length === 0 ? (
          <div className="empty-state">
            <h3>没有快照</h3>
            <p>当前时间范围内没有历史市场快照。</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>observed_at</th>
                  <th>best_ask</th>
                  <th>best_bid</th>
                  <th>ask_count</th>
                  <th>bid_count</th>
                  <th>estimated_volume</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((snapshot) => (
                  <tr key={snapshot.id}>
                    <td>{formatDateTime(snapshot.observed_at)}</td>
                    <td>{formatDecimal(snapshot.best_ask)}</td>
                    <td>{formatDecimal(snapshot.best_bid)}</td>
                    <td>{formatOptionalNumber(snapshot.ask_count)}</td>
                    <td>{formatOptionalNumber(snapshot.bid_count)}</td>
                    <td>{formatDecimal(snapshot.estimated_volume)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatOptionalNumber(value: number | null): string {
  return value === null ? "—" : String(value);
}

function toSnapshotQuery(params: Record<string, string | string[] | undefined>): SnapshotQuery {
  return {
    from: readParam(params.from),
    to: readParam(params.to),
    limit: readParam(params.limit) ?? "500",
    order: readParam(params.order) === "desc" ? "desc" : ("asc" satisfies SortOrder)
  };
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

async function loadItemDetail(
  itemId: string,
  query: SnapshotQuery
): Promise<{ item: ItemDetail; snapshots: MarketSnapshot[] } | { error: ApiError }> {
  try {
    const [item, snapshots] = await Promise.all([getItem(itemId), getItemSnapshots(itemId, query)]);
    return { item, snapshots };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

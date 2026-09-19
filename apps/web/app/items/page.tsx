import Link from "next/link";
import type { ReactNode } from "react";
import { getItems, toDisplayError } from "../../lib/api-client";
import { formatDateTime } from "../../lib/formatters";
import type { ApiError, ItemListQuery, ItemSummary, PaginatedItemsResponse, SortField, SortOrder } from "../../lib/types";
import { MarketHeader } from "../market-header";
import { ItemsFilterForm } from "./items-filter-form";

export const dynamic = "force-dynamic";

type ItemsPageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function ItemsPage({ searchParams }: ItemsPageProps) {
  const query = toItemListQuery(await searchParams);
  const result = await loadItems(query);

  return (
    <main className="art-shell">
      <MarketHeader active="catalog" />
      <section className="deco-page-title">
        <div><p className="deco-kicker">VEHICLE CATALOG</p><h1>载具目录</h1><p>仅展示人工审核导入的数据；缺失价格不会被填成零或示例值。</p></div>
        {"data" in result ? <div className="deco-total"><strong>{result.data.total}</strong><span>个可浏览条目</span></div> : null}
      </section>
      <ItemsFilterForm query={query} />

      {"error" in result ? <CatalogError error={result.error} /> : <Catalog data={result.data} query={query} />}
    </main>
  );
}

function Catalog({ data, query }: { data: PaginatedItemsResponse; query: ItemListQuery }) {
  const hasPrevious = data.page > 1;
  const hasNext = data.total_pages > 0 && data.page < data.total_pages;
  return (
    <section className="deco-list-section deco-catalog-page" aria-labelledby="catalog-heading">
      <div className="deco-section-heading">
        <div><p className="deco-kicker">CURRENT BOOK</p><h2 id="catalog-heading">审核数据目录</h2></div>
        <div className="deco-pagination" aria-label="分页">
          <PageLink disabled={!hasPrevious} page={Math.max(data.page - 1, 1)} query={query}>← 上一页</PageLink>
          <span>{data.page} / {Math.max(data.total_pages, 1)}</span>
          <PageLink disabled={!hasNext} page={data.page + 1} query={query}>下一页 →</PageLink>
        </div>
      </div>
      {data.items.length === 0 ? (
        <div className="deco-empty-large"><span aria-hidden="true">00</span><div><strong>当前条件没有载具</strong><p>保留筛选条件继续调整，或重置后查看全部审核条目。</p><Link href="/items">重置筛选 →</Link></div></div>
      ) : (
        <div className="deco-catalog-list">
          <div className="deco-list-head" aria-hidden="true"><span>载具</span><span>最佳买价</span><span>最佳卖价</span><span>观测状态</span><span /></div>
          {data.items.map((item) => <CatalogRow item={item} key={item.id} />)}
        </div>
      )}
    </section>
  );
}

function CatalogRow({ item }: { item: ItemSummary }) {
  const book = item.current_order_book;
  return (
    <Link className="deco-catalog-row" href={`/items/${item.id}`}>
      <div className="deco-item-identity"><span className="deco-item-mark" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span><div><strong>{item.name}</strong><small>{item.category}{item.rarity ? ` · ${item.rarity}` : ""}<br />{item.external_key}</small></div></div>
      <span className="deco-number">{book ? `${book.best_buy.canonical_display_text} GJN` : "—"}</span>
      <span className="deco-number">{book ? `${book.best_sell.canonical_display_text} GJN` : "—"}</span>
      <span className={`deco-data-state ${book?.freshness === "stale" ? "stale" : ""}`}><b>{book ? (book.freshness === "fresh" ? "当前" : "已过期") : statusLabel(item)}</b><small>{book ? formatDateTime(book.captured_at) : "无观测时间"}</small></span>
      <b className="deco-row-action">查看 →</b>
    </Link>
  );
}

function CatalogError({ error }: { error: ApiError }) {
  return <section className="deco-error" aria-live="polite"><span aria-hidden="true">!</span><div><strong>{error.code === "api_unreachable" ? "API 不可访问" : "目录加载失败"}</strong><p>{error.message}</p></div></section>;
}

function PageLink({ children, disabled, page, query }: { children: ReactNode; disabled: boolean; page: number; query: ItemListQuery }) {
  if (disabled) return <span aria-disabled="true">{children}</span>;
  return <Link href={{ pathname: "/items", query: cleanQuery({ ...query, page: String(page) }) }}>{children}</Link>;
}

function statusLabel(item: ItemSummary): string {
  return item.current_order_book_status === "contract_error" ? "合同错误" : "数据不足";
}

function toItemListQuery(params: Record<string, string | string[] | undefined>): ItemListQuery {
  return { page: readParam(params.page) ?? "1", page_size: readParam(params.page_size) ?? "20", search: readParam(params.search), category: readParam(params.category), rarity: readParam(params.rarity), is_active: readParam(params.is_active), sort: readSort(params.sort), order: readOrder(params.order) };
}

function readParam(value: string | string[] | undefined): string | undefined { return Array.isArray(value) ? value[0] : value; }
function readSort(value: string | string[] | undefined): SortField { const sort = readParam(value); return sort === "created_at" || sort === "updated_at" ? sort : "name"; }
function readOrder(value: string | string[] | undefined): SortOrder { return readParam(value) === "desc" ? "desc" : "asc"; }
function cleanQuery(query: ItemListQuery): Record<string, string> { return Object.fromEntries(Object.entries(query).filter((entry): entry is [string, string] => Boolean(entry[1]))); }

async function loadItems(query: ItemListQuery): Promise<{ data: PaginatedItemsResponse } | { error: ApiError }> {
  try { return { data: await getItems(query) }; } catch (error) { return { error: toDisplayError(error) }; }
}

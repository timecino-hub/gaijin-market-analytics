import Link from "next/link";
import type { ReactNode } from "react";
import { getItems, toDisplayError } from "../../lib/api-client";
import { formatBoolean, formatDateTime, formatDecimal, formatOptionalText } from "../../lib/formatters";
import type { ApiError, ItemListQuery, PaginatedItemsResponse, SortField, SortOrder } from "../../lib/types";
import { ItemsFilterForm } from "./items-filter-form";

type ItemsPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ItemsPage({ searchParams }: ItemsPageProps) {
  const params = await searchParams;
  const query = toItemListQuery(params);
  const dataResult = await loadItems(query);

  if ("error" in dataResult) {
    return (
      <main className="page-shell">
        <Header />
        <ItemsFilterForm />
        <ErrorPanel error={dataResult.error} />
      </main>
    );
  }

  const data = dataResult.data;
  const previousPage = Math.max(data.page - 1, 1);
  const nextPage = data.page + 1;
  const hasPrevious = data.page > 1;
  const hasNext = data.total_pages > 0 && data.page < data.total_pages;

  return (
    <main className="page-shell">
      <Header />
      <ItemsFilterForm />

      <section className="panel" aria-labelledby="items-heading">
        <div className="section-heading">
          <div>
            <h2 id="items-heading">商品列表</h2>
            <p>
              第 {data.page} 页，共 {data.total_pages} 页；总商品数 {data.total}
            </p>
          </div>
          <div className="pagination" aria-label="分页">
            <PageLink disabled={!hasPrevious} page={previousPage} query={query}>
              上一页
            </PageLink>
            <PageLink disabled={!hasNext} page={nextPage} query={query}>
              下一页
            </PageLink>
          </div>
        </div>

          {data.items.length === 0 ? (
            <div className="empty-state">
              <h3>没有商品</h3>
              <p>当前数据库或筛选条件下没有可浏览的已导入商品。</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>external_key</th>
                    <th>分类</th>
                    <th>稀有度</th>
                    <th>状态</th>
                    <th>best_ask</th>
                    <th>best_bid</th>
                    <th>观测时间</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link href={`/items/${item.id}`}>{item.name}</Link>
                      </td>
                      <td>{item.external_key}</td>
                      <td>{item.category}</td>
                      <td>{formatOptionalText(item.rarity)}</td>
                      <td>{formatBoolean(item.is_active)}</td>
                      {item.latest_snapshot ? (
                        <>
                          <td>{formatDecimal(item.latest_snapshot.best_ask)}</td>
                          <td>{formatDecimal(item.latest_snapshot.best_bid)}</td>
                          <td>{formatDateTime(item.latest_snapshot.observed_at)}</td>
                        </>
                      ) : (
                        <td colSpan={3}>暂无市场快照</td>
                      )}
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

function Header() {
  return (
    <header className="page-header">
      <nav className="page-nav" aria-label="页面导航">
        <Link href="/" className="back-link">
          返回首页
        </Link>
        <Link href="/imports" className="back-link">
          导入 CSV
        </Link>
      </nav>
      <h1>已导入市场数据</h1>
      <p>仅浏览 CSV、JSON、手动或明确授权来源导入的数据，不展示虚假涨跌或收益。</p>
    </header>
  );
}

function ErrorPanel({ error }: { error: { code: string; message: string } }) {
  return (
    <section className="error-state" aria-live="polite">
      <h2>{error.code === "api_unreachable" ? "API 不可访问" : "无法加载商品"}</h2>
      <p>{error.message}</p>
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
  query?: ItemListQuery;
}) {
  if (disabled) {
    return (
      <span className="button-disabled" aria-disabled="true">
        {children}
      </span>
    );
  }

  const nextQuery = { ...query, page: String(page) };
  return (
    <Link className="button-link" href={{ pathname: "/items", query: cleanQuery(nextQuery) }}>
      {children}
    </Link>
  );
}

function toItemListQuery(params: Record<string, string | string[] | undefined>): ItemListQuery {
  return {
    page: readParam(params.page) ?? "1",
    page_size: readParam(params.page_size) ?? "20",
    search: readParam(params.search),
    category: readParam(params.category),
    rarity: readParam(params.rarity),
    is_active: readParam(params.is_active),
    sort: readSort(params.sort),
    order: readOrder(params.order)
  };
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function readSort(value: string | string[] | undefined): SortField {
  const sort = readParam(value);
  return sort === "created_at" || sort === "updated_at" ? sort : "name";
}

function readOrder(value: string | string[] | undefined): SortOrder {
  return readParam(value) === "desc" ? "desc" : "asc";
}

function cleanQuery(query: ItemListQuery): Record<string, string> {
  return Object.fromEntries(
    Object.entries(query).filter((entry): entry is [string, string] => Boolean(entry[1]))
  );
}

async function loadItems(
  query: ItemListQuery
): Promise<{ data: PaginatedItemsResponse } | { error: ApiError }> {
  try {
    return { data: await getItems(query) };
  } catch (error) {
    return { error: toDisplayError(error) };
  }
}

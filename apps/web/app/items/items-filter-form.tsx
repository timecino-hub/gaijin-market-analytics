import Link from "next/link";
import type { ItemListQuery } from "../../lib/types";

export function ItemsFilterForm({ query }: { query: ItemListQuery }) {
  return (
    <form className="deco-filter" action="/items" method="get" aria-label="载具筛选">
      <label className="deco-filter-search">
        <span>搜索载具</span>
        <input name="search" type="search" defaultValue={query.search ?? ""} placeholder="名称或 external key" />
      </label>
      <label><span>分类</span><input name="category" defaultValue={query.category ?? ""} placeholder="全部分类" /></label>
      <label><span>稀有度</span><input name="rarity" defaultValue={query.rarity ?? ""} placeholder="全部稀有度" /></label>
      <label>
        <span>状态</span>
        <select name="is_active" defaultValue={query.is_active ?? ""}>
          <option value="">全部</option><option value="true">启用</option><option value="false">停用</option>
        </select>
      </label>
      <label>
        <span>排序</span>
        <select name="sort" defaultValue={query.sort ?? "name"}>
          <option value="name">名称</option><option value="created_at">创建时间</option><option value="updated_at">更新时间</option>
        </select>
      </label>
      <input name="order" type="hidden" value={query.order ?? "asc"} />
      <button type="submit">应用筛选</button>
      <Link href="/items">重置</Link>
    </form>
  );
}

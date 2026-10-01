import Link from "next/link";
import type { VehiclePotentialListState } from "../../lib/vehicle-potential-state";

export function VehiclePotentialFilterForm({
  form
}: {
  form: VehiclePotentialListState["form"];
}) {
  return (
    <form
      className="deco-opportunity-filter deco-potential-filter"
      action="/opportunities"
      method="get"
    >
      <label>
        搜索载具
        <input name="q" defaultValue={form.q} maxLength={80} placeholder="载具名称或编号" type="search" />
      </label>

      <label>
        比较顺序
        <select name="sort" defaultValue={form.sort}>
          <option value="percentile">历史价格分位：低到高</option>
          <option value="median_room">到历史中位价净空间：高到低</option>
          <option value="recent_volume">近30天成交量中位数：高到低</option>
          <option value="freshness">成交日期：新到旧</option>
        </select>
      </label>

      <label>
        到历史中位价净空间
        <select name="room" defaultValue={form.room}>
          <option value="all">全部</option>
          <option value="50">至少50%，所需卖价在上限内</option>
          <option value="40">至少40%，所需卖价在上限内</option>
        </select>
      </label>

      <label>
        显示范围
        <select name="ranked_only" defaultValue={String(form.rankedOnly)}>
          <option value="false">全部载具（含历史不足）</option>
          <option value="true">仅显示可定位的载具</option>
        </select>
      </label>

      <label>
        商品状态
        <select name="include_inactive" defaultValue={String(form.includeInactive)}>
          <option value="false">仅启用商品</option>
          <option value="true">包含停用商品</option>
        </select>
      </label>

      <label>
        查询时点（可选）
        <input
          name="as_of"
          defaultValue={form.asOf}
          placeholder="2026-09-01T00:00:00Z"
          inputMode="text"
        />
        <span className="deco-field-hint">
          带时区时间；留空为当前 UTC
        </span>
      </label>

      <div className="deco-opportunity-actions">
        <button type="submit">应用筛选</button>
        <Link href="/opportunities">重置条件</Link>
      </div>
    </form>
  );
}

import Link from "next/link";
import type { VehiclePotentialListState } from "../../lib/vehicle-potential-state";

export function VehiclePotentialFilterForm({
  form
}: {
  form: VehiclePotentialListState["form"];
}) {
  return (
    <form
      className="deco-opportunity-filter deco-compact-filter"
      action="/opportunities"
      method="get"
    >
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
        as_of（可选）
        <input
          name="as_of"
          defaultValue={form.asOf}
          placeholder="2026-09-01T00:00:00Z"
          inputMode="text"
        />
        <span className="deco-field-hint">
          ISO-8601 带时区；留空为当前 UTC
        </span>
      </label>

      <div className="deco-opportunity-actions">
        <button type="submit">重新定位</button>
        <Link href="/opportunities">重置条件</Link>
      </div>
    </form>
  );
}

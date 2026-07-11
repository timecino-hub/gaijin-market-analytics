import Link from "next/link";
import type { OpportunityListState } from "../../lib/opportunity-list-state";
import { OPPORTUNITY_HORIZONS, OPPORTUNITY_PAGE_SIZES } from "../../lib/opportunity-list-state";

export function OpportunityFilterForm({ form }: { form: OpportunityListState["form"] }) {
  return (
    <form className="toolbar opportunity-toolbar" action="/opportunities" method="get">
      <input type="hidden" name="page" value="1" />

      <label>
        分析周期
        <select name="horizon" defaultValue={form.horizon}>
          {OPPORTUNITY_HORIZONS.map((horizon) => (
            <option key={horizon} value={horizon}>
              {horizon} 天
            </option>
          ))}
        </select>
      </label>

      <label>
        最低综合分
        <input
          name="min_score"
          type="number"
          min="0"
          max="100"
          step="0.01"
          defaultValue={form.minimumScore}
        />
      </label>

      <label>
        排名范围
        <select name="eligible_only" defaultValue={String(form.eligibleOnly)}>
          <option value="true">仅显示合格机会</option>
          <option value="false">包含不合格诊断</option>
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
        搜索
        <input name="search" defaultValue={form.search} placeholder="名称或 external_key" />
      </label>

      <label>
        分类
        <input name="category" defaultValue={form.category} placeholder="精确匹配，可留空" />
      </label>

      <label>
        稀有度
        <input name="rarity" defaultValue={form.rarity} placeholder="精确匹配，可留空" />
      </label>

      <label>
        as_of（可选）
        <input
          name="as_of"
          defaultValue={form.asOf}
          placeholder="2026-06-29T00:00:00Z"
          inputMode="text"
        />
        <span className="field-hint">带时区的 ISO-8601 时间；留空使用 API 当前 UTC。</span>
      </label>

      <label>
        每页数量
        <select name="page_size" defaultValue={form.pageSize}>
          {OPPORTUNITY_PAGE_SIZES.map((pageSize) => (
            <option key={pageSize} value={pageSize}>
              {pageSize}
            </option>
          ))}
        </select>
      </label>

      <div className="form-actions opportunity-filter-actions">
        <button type="submit">计算排行榜</button>
        <Link className="button-link plain-button" href="/opportunities">
          重置条件
        </Link>
      </div>
    </form>
  );
}

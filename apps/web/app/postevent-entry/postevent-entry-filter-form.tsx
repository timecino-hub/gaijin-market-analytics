import Link from "next/link";
import type { PostEventEntryListState } from "../../lib/postevent-entry-state";

export function PostEventEntryFilterForm({ form }: { form: PostEventEntryListState["form"] }) {
  return (
    <form className="deco-opportunity-filter deco-postevent-filter" action="/postevent-entry" method="get">
      <label>
        显示范围
        <select name="candidates_only" defaultValue={String(form.candidatesOnly)}>
          <option value="false">全部载具（含未达门槛与已关闭窗口）</option>
          <option value="true">仅显示入场候选</option>
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
          placeholder="2026-05-07T00:00:00Z"
          inputMode="text"
        />
        <span className="deco-field-hint">
          带时区的 ISO-8601 时间；留空使用当前 UTC。回放历史窗口时填活动结束后的日期。
        </span>
      </label>

      <div className="deco-opportunity-actions">
        <button type="submit">重新评估</button>
        <Link href="/postevent-entry">重置条件</Link>
      </div>
    </form>
  );
}

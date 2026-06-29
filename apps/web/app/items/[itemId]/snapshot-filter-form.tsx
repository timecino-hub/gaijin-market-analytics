"use client";

import { useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";
import { datetimeLocalToIso } from "../../../lib/analysis-validation";
import { itemDetailPath, mergeSnapshotQuery, removeSnapshotQuery } from "../../../lib/analysis-url-state";

export function SnapshotFilterForm({ itemId }: { itemId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const from = datetimeLocalToIso(String(form.get("from") ?? ""));
    const to = datetimeLocalToIso(String(form.get("to") ?? ""));
    const next = mergeSnapshotQuery(searchParams, {
      from: from.ok ? from.value : undefined,
      to: to.ok ? to.value : undefined,
      limit: String(form.get("limit") ?? ""),
      order: String(form.get("order") ?? "asc")
    });
    router.push(itemDetailPath(itemId, next));
  }

  return (
    <form className="toolbar" onSubmit={submit} aria-label="历史快照筛选">
      <label>
        开始时间
        <input
          name="from"
          type="datetime-local"
          defaultValue={toDateTimeLocal(searchParams.get("from"))}
        />
      </label>
      <label>
        结束时间
        <input name="to" type="datetime-local" defaultValue={toDateTimeLocal(searchParams.get("to"))} />
      </label>
      <label>
        条数
        <input
          name="limit"
          type="number"
          min="1"
          max="2000"
          defaultValue={searchParams.get("limit") ?? "500"}
        />
      </label>
      <label>
        顺序
        <select name="order" defaultValue={searchParams.get("order") ?? "asc"}>
          <option value="asc">升序</option>
          <option value="desc">降序</option>
        </select>
      </label>
      <div className="form-actions">
        <button type="submit">应用</button>
        <button
          type="button"
          onClick={() => router.push(itemDetailPath(itemId, removeSnapshotQuery(searchParams)))}
        >
          清除
        </button>
      </div>
    </form>
  );
}

function toDateTimeLocal(value: string | null): string {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

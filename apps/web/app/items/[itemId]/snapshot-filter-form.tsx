"use client";

import { useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";

export function SnapshotFilterForm({ itemId }: { itemId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams(searchParams);
    setDateTime(next, "from", String(form.get("from") ?? ""));
    setDateTime(next, "to", String(form.get("to") ?? ""));
    setText(next, "limit", String(form.get("limit") ?? ""));
    setText(next, "order", String(form.get("order") ?? "asc"));
    router.push(`/items/${itemId}?${next.toString()}`);
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
        <button type="button" onClick={() => router.push(`/items/${itemId}`)}>
          清除
        </button>
      </div>
    </form>
  );
}

function setText(params: URLSearchParams, key: string, value: string) {
  const trimmed = value.trim();
  if (trimmed) {
    params.set(key, trimmed);
  } else {
    params.delete(key);
  }
}

function setDateTime(params: URLSearchParams, key: string, value: string) {
  if (!value) {
    params.delete(key);
    return;
  }

  params.set(key, new Date(value).toISOString());
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

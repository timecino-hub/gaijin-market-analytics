"use client";

import { useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";

export function ItemsFilterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  function apply(values: Record<string, string>) {
    const next = new URLSearchParams(searchParams);
    next.set("page", "1");

    for (const [key, value] of Object.entries(values)) {
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
    }

    router.push(`/items?${next.toString()}`);
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    apply({ search: String(data.get("search") ?? "").trim() });
  }

  return (
    <div className="toolbar" aria-label="商品筛选">
      <form className="search-form" onSubmit={submitSearch}>
        <label htmlFor="search">搜索</label>
        <div className="inline-controls">
          <input
            id="search"
            name="search"
            type="search"
            defaultValue={searchParams.get("search") ?? ""}
            placeholder="名称或 external_key"
          />
          <button type="submit">搜索</button>
        </div>
      </form>

      <label>
        分类
        <input
          type="text"
          defaultValue={searchParams.get("category") ?? ""}
          onBlur={(event) => apply({ category: event.currentTarget.value.trim() })}
          placeholder="例如 vehicle"
        />
      </label>

      <label>
        稀有度
        <input
          type="text"
          defaultValue={searchParams.get("rarity") ?? ""}
          onBlur={(event) => apply({ rarity: event.currentTarget.value.trim() })}
          placeholder="例如 rare"
        />
      </label>

      <label>
        状态
        <select
          defaultValue={searchParams.get("is_active") ?? ""}
          onChange={(event) => apply({ is_active: event.currentTarget.value })}
        >
          <option value="">全部</option>
          <option value="true">启用</option>
          <option value="false">停用</option>
        </select>
      </label>

      <label>
        排序
        <select
          defaultValue={searchParams.get("sort") ?? "name"}
          onChange={(event) => apply({ sort: event.currentTarget.value })}
        >
          <option value="name">名称</option>
          <option value="created_at">创建时间</option>
          <option value="updated_at">更新时间</option>
        </select>
      </label>

      <label>
        顺序
        <select
          defaultValue={searchParams.get("order") ?? "asc"}
          onChange={(event) => apply({ order: event.currentTarget.value })}
        >
          <option value="asc">升序</option>
          <option value="desc">降序</option>
        </select>
      </label>
    </div>
  );
}

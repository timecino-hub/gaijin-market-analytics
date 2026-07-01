import type { ReviewStatus } from "../../lib/types";

export function reviewStatusLabel(status: ReviewStatus | string): string {
  const labels: Record<string, string> = {
    processing: "处理中",
    pending_review: "待复核",
    confirmed: "已确认",
    confirmed_with_edits: "修改后确认",
    rejected: "已拒绝",
    unreadable: "无法读取",
    failed: "处理失败",
    expired: "已过期"
  };
  return labels[status] ?? status;
}

export function fieldLabel(field: string): string {
  const labels: Record<string, string> = {
    item_name: "商品名称",
    best_bid: "Best bid",
    best_ask: "Best ask",
    total_bid_quantity: "求购数量",
    total_ask_quantity: "售单数量",
    observed_at: "观测时间"
  };
  return labels[field] ?? field;
}

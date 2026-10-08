import { getCurrentOrderBook, getItem, getItemHistoricalTrades, toDisplayError } from "./api-client.ts";
import type { ApiError, CurrentOrderBook, ItemDetail, ItemHistoricalTrades } from "./types.ts";

export type ItemDetailData = {
  item: ItemDetail;
  orderBook: CurrentOrderBook | null;
  orderBookError: ApiError | null;
  history: ItemHistoricalTrades | null;
  historyError: ApiError | null;
};

export async function loadItemDetail(itemId: string): Promise<ItemDetailData | { error: ApiError }> {
  const [item, book, history] = await Promise.allSettled([
    getItem(itemId),
    getCurrentOrderBook(itemId),
    getItemHistoricalTrades(itemId)
  ]);
  if (item.status === "rejected") return { error: toDisplayError(item.reason) };
  const bookError = book.status === "rejected" ? toDisplayError(book.reason) : null;
  return {
    item: item.value,
    orderBook: book.status === "fulfilled" ? book.value : null,
    orderBookError: bookError?.code === "order_book_not_found" ? null : bookError,
    history: history.status === "fulfilled" ? history.value : null,
    historyError: history.status === "rejected" ? toDisplayError(history.reason) : null
  };
}

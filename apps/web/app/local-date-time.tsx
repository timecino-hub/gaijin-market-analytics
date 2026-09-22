"use client";

import { useEffect, useState } from "react";
import { formatDateTime, formatDateTimeUtc } from "../lib/formatters";

/**
 * A timestamp shown in the reader's own time zone.
 *
 * These pages are server-rendered, so formatting on the server would print the
 * container's zone — UTC on this host, and something arbitrary on any other.
 * The server therefore emits the UTC rendering, and the browser re-formats it
 * in the reader's zone once hydrated.
 *
 * The exact instant is always in `dateTime`, so the value stays machine-readable
 * and unambiguous in the moment before that swap, and for a reader with
 * JavaScript off it stays a correct UTC timestamp rather than a wrong local one.
 */
export function LocalDateTime({ value }: { value: string | null }) {
  const [text, setText] = useState(() => formatDateTimeUtc(value));

  // The one thing this component must never do is render something different
  // from the server on its first pass — that is a hydration mismatch. Deriving
  // the text during render (from a store, say) cannot promise that, so the
  // swap happens here instead, after the first render has already matched.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the deferred swap is the point: it is what keeps the first render identical to the server's
    setText(formatDateTime(value));
  }, [value]);

  if (value === null) {
    return <>{text}</>;
  }

  return <time dateTime={value}>{text}</time>;
}

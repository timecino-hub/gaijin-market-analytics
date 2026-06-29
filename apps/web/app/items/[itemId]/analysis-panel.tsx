"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  analysisReasonLabel,
  analysisStatusDescription,
  analysisStatusLabel,
  formatCurrencyDisplay,
  formatDecimalDisplay,
  formatDecimalPercent,
  formatScore
} from "../../../lib/analysis-display";
import { ANALYSIS_HORIZONS, validateAnalysisForm } from "../../../lib/analysis-validation";
import type { AnalysisFormValues } from "../../../lib/analysis-validation";
import { getItemAnalysis, toDisplayError } from "../../../lib/api-client";
import { formatDateTime } from "../../../lib/formatters";
import { itemDetailPath, mergeAnalysisQuery } from "../../../lib/analysis-url-state";
import type { InitialAnalysisState } from "../../../lib/analysis-url-state";
import type { ApiError, ItemAnalysisQuery, ItemAnalysisResponse } from "../../../lib/types";

type AnalysisPanelProps = {
  itemId: string;
  itemName: string;
  initialState: InitialAnalysisState;
};

type Phase = "idle" | "loading" | "ok" | "analysis_status" | "http_error";

export function AnalysisPanel({ itemId, itemName, initialState }: AnalysisPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [formValues, setFormValues] = useState<AnalysisFormValues>(initialState.formValues);
  const [submittedQuery, setSubmittedQuery] = useState<ItemAnalysisQuery | null>(
    initialState.canAutoRun ? initialState.query : null
  );
  const [result, setResult] = useState<ItemAnalysisResponse | null>(null);
  const [displayError, setDisplayError] = useState<string | null>(initialState.error);
  const [apiError, setApiError] = useState<ApiError | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const requestSequence = useRef(0);
  const abortController = useRef<AbortController | null>(null);
  const autoRunKey = useRef<string | null>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    return () => {
      isMounted.current = false;
      abortController.current?.abort();
    };
  }, []);

  const runAnalysis = useCallback(
    async (query: ItemAnalysisQuery) => {
      const sequence = requestSequence.current + 1;
      requestSequence.current = sequence;
      abortController.current?.abort();
      const controller = new AbortController();
      abortController.current = controller;

      setSubmittedQuery(query);
      setPhase("loading");
      setDisplayError(null);
      setApiError(null);

      try {
        const response = await getItemAnalysis(itemId, query, controller.signal);
        if (!isMounted.current || requestSequence.current !== sequence) {
          return;
        }

        setResult(response);
        setPhase(response.status === "ok" ? "ok" : "analysis_status");
      } catch (error) {
        if (isAbortError(error) || !isMounted.current || requestSequence.current !== sequence) {
          return;
        }

        setApiError(toDisplayError(error));
        setPhase("http_error");
      }
    },
    [itemId]
  );

  useEffect(() => {
    if (!initialState.canAutoRun) {
      return;
    }

    const key = JSON.stringify(initialState.query);
    if (autoRunKey.current === key) {
      return;
    }
    autoRunKey.current = key;

    const timeoutId = window.setTimeout(() => {
      void runAnalysis(initialState.query);
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [initialState, runAnalysis]);

  function submitAnalysis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = validateAnalysisForm(formValues);
    if (!validation.ok) {
      setDisplayError(validation.message);
      setApiError(null);
      setPhase("idle");
      return;
    }

    const nextParams = mergeAnalysisQuery(searchParams, validation.value);
    router.push(itemDetailPath(itemId, nextParams));
    void runAnalysis(validation.value);
  }

  return (
    <section className="panel analysis-panel" aria-labelledby="analysis-heading">
      <div className="section-heading">
        <div>
          <h2 id="analysis-heading">即时基线分析</h2>
          <p>RuleBasedV1 只读取已导入快照并即时计算；结果不是收益保证或交易建议。</p>
        </div>
      </div>

      <FeePolicySummary result={result} />

      <form className="analysis-form" onSubmit={submitAnalysis} aria-label="即时基线分析条件">
        <fieldset className="horizon-options" disabled={phase === "loading"}>
          <legend>分析周期</legend>
          {ANALYSIS_HORIZONS.map((horizon) => (
            <label key={horizon} className="choice-pill">
              <input
                type="radio"
                name="horizon"
                value={horizon}
                checked={formValues.horizon === horizon}
                onChange={(event) =>
                  setFormValues((current) => ({ ...current, horizon: event.currentTarget.value }))
                }
              />
              <span>{horizon} 天</span>
            </label>
          ))}
        </fieldset>

        <div className="analysis-controls">
          <label>
            分析基准时间（可选）
            <input
              name="as_of"
              type="datetime-local"
              value={formValues.asOfLocal}
              onChange={(event) =>
                setFormValues((current) => ({ ...current, asOfLocal: event.currentTarget.value }))
              }
              disabled={phase === "loading"}
            />
            <span className="field-hint">用于历史复现；留空时由 API 使用当前 UTC，不是预测终点。</span>
          </label>

          <div className="form-actions analysis-actions">
            <button type="submit" disabled={phase === "loading"}>
              {phase === "loading" ? "正在分析" : "运行分析"}
            </button>
          </div>
        </div>
      </form>

      <div className="analysis-message" aria-live="polite">
        {displayError ? <p className="inline-error">{displayError}</p> : null}
        {apiError ? (
          <div className="error-state compact-error">
            <h3>{apiError.code === "api_unreachable" ? "API 不可访问" : "分析请求失败"}</h3>
            <p>{apiError.message}</p>
          </div>
        ) : null}
        {phase === "loading" ? <p className="muted-text">正在读取已导入快照并运行 RuleBasedV1...</p> : null}
        {phase === "idle" && !displayError ? (
          <p className="muted-text">选择周期后点击“运行分析”。费用政策固定，页面不会提供手续费输入。</p>
        ) : null}
      </div>

      {result ? (
        <AnalysisResultView
          itemName={itemName}
          result={result}
          submittedQuery={submittedQuery}
          isOk={phase === "ok"}
        />
      ) : null}
    </section>
  );
}

function FeePolicySummary({ result }: { result: ItemAnalysisResponse | null }) {
  const policy = result?.effective_inputs.fee_policy;
  return (
    <div className="detail-grid compact" aria-label="固定费用政策">
      <Info label="市场名义手续费" value={policy ? formatDecimalPercent(policy.nominal_fee_rate) : "15%"} />
      <Info label="结算最小单位" value={`${policy?.currency_quantum ?? "0.01"} GJN`} />
      <Info label="结算规则" value="扣费后的卖家所得向下取整" />
      <Info
        label="费用政策版本"
        value={policy ? `${policy.name} ${policy.version}` : "gaijin_market 1.0.0"}
      />
    </div>
  );
}

function AnalysisResultView({
  itemName,
  result,
  submittedQuery,
  isOk
}: {
  itemName: string;
  result: ItemAnalysisResponse;
  submittedQuery: ItemAnalysisQuery | null;
  isOk: boolean;
}) {
  return (
    <div className="analysis-result">
      <div className={isOk ? "analysis-status ok" : "analysis-status warning"}>
        <strong>{analysisStatusLabel(result.status)}</strong>
        <span>{analysisStatusDescription(result.status)}</span>
      </div>

      <div className="detail-grid compact">
        <Info label="商品名称" value={result.item_name || itemName} />
        <Info label="请求周期" value={submittedQuery ? `${submittedQuery.horizon} 天` : "—"} />
        <Info label="请求 as_of" value={submittedQuery?.as_of ? formatDateTime(submittedQuery.as_of) : "—"} />
        <Info label="实际周期" value={`${result.effective_inputs.horizon} 天`} />
        <Info label="实际 as_of" value={formatDateTime(result.effective_inputs.as_of)} />
        <Info
          label="maximum snapshot age"
          value={`${result.effective_inputs.maximum_snapshot_age_seconds} 秒`}
        />
        <Info
          label="minimum snapshot count"
          value={String(result.effective_inputs.minimum_snapshot_count)}
        />
        <Info label="strategy_name" value={result.strategy_name} />
        <Info label="strategy_version" value={result.strategy_version} />
        <Info label="feature_version" value={result.feature_version} />
        <Info label="status" value={result.status} />
        <Info label="observation_count" value={String(result.observation_count)} />
        <Info label="first_observation_at" value={formatDateTime(result.first_observation_at)} />
        <Info label="last_observation_at" value={formatDateTime(result.last_observation_at)} />
      </div>

      <div className="reason-list" aria-label="reason codes">
        {result.reason_codes.length > 0 ? (
          result.reason_codes.map((reasonCode) => (
            <span key={reasonCode} className="reason-chip">
              {analysisReasonLabel(reasonCode)}
              <small>{reasonCode}</small>
            </span>
          ))
        ) : (
          <span className="reason-chip">暂无 reason code</span>
        )}
      </div>

      <div className="table-wrap analysis-metrics">
        <table>
          <tbody>
            <Metric label="当前最低卖价" value={formatCurrencyDisplay(result.current_ask)} />
            <Metric label="当前最高买价" value={formatCurrencyDisplay(result.current_bid)} />
            <Metric label="基线参考卖价" value={formatCurrencyDisplay(result.reference_sell_price)} />
            <Metric label="卖家结算所得" value={formatCurrencyDisplay(result.sale_proceeds)} />
            <Metric label="实际费用差额" value={formatCurrencyDisplay(result.fee_amount)} />
            <Metric label="毛利润" value={formatCurrencyDisplay(result.gross_profit)} />
            <Metric label="手续费后净利润" value={formatCurrencyDisplay(result.net_profit)} />
            <Metric label="净 ROI" value={formatDecimalPercent(result.net_roi)} />
            <Metric label="离散盈亏平衡卖价" value={formatCurrencyDisplay(result.break_even_sell_price)} />
            <Metric label="绝对价差" value={formatCurrencyDisplay(result.spread_absolute)} />
            <Metric label="相对价差" value={formatDecimalPercent(result.spread_ratio)} />
            <Metric label="bid 中位数" value={formatDecimalDisplay(result.median_bid)} />
            <Metric label="ask 中位数" value={formatDecimalDisplay(result.median_ask)} />
            <Metric label="稳健波动指标" value={formatDecimalDisplay(result.price_volatility)} />
            <Metric label="流动性评分" value={formatScore(result.liquidity_score)} />
            <Metric label="风险评分" value={formatScore(result.risk_score)} />
            <Metric label="数据置信度" value={formatScore(result.confidence_score)} />
          </tbody>
        </table>
      </div>

      <div className="notice analysis-disclaimer">
        <p>
          Gaijin Market 按 15% 的名义费率计算，并将卖家结算所得向下取整到 0.01 GJN。由于取整，
          实际费用差额可能并不精确等于挂牌价的 15%。基线参考卖价不是未来价格保证，数据置信度不是盈利概率，
          结果不构成交易建议或收益保证。
        </p>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{value}</td>
    </tr>
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

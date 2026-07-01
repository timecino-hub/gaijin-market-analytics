"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import {
  clearLocalRecognitionReviews,
  confirmLocalRecognitionReview,
  getItems,
  getLocalRecognitionCapabilities,
  getLocalRecognitionReview,
  getLocalRecognitionReviews,
  markLocalRecognitionReviewUnreadable,
  patchLocalRecognitionReview,
  rejectLocalRecognitionReview,
  toDisplayError,
  uploadLocalRecognitionReview
} from "../../lib/api-client";
import type {
  ApiError,
  ItemSummary,
  LocalRecognitionCapabilities,
  LocalRecognitionReview,
  LocalRecognitionReviewList
} from "../../lib/types";
import { formatDateTime } from "../../lib/formatters";
import { reviewStatusLabel } from "./review-labels";
import {
  MAX_SCREENSHOT_BYTES,
  applyReviewFormUpdate,
  formFromReview,
  mergeReviewsByPolling,
  payloadFromForm,
  sortedReviewSections,
  validateReviewForm,
  validateScreenshotFile,
  type ReviewFormState
} from "./review-validation";

type UploadState = {
  phase: "idle" | "selected" | "uploading" | "error";
  file: File | null;
  previewUrl: string | null;
  error: ApiError | null;
};

export function ScreenRecognitionWorkspace() {
  const [capabilities, setCapabilities] = useState<LocalRecognitionCapabilities | null>(null);
  const [reviewList, setReviewList] = useState<LocalRecognitionReviewList | null>(null);
  const [selectedReview, setSelectedReview] = useState<LocalRecognitionReview | null>(null);
  const [form, setForm] = useState<ReviewFormState | null>(null);
  const [dirty, setDirty] = useState(false);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadState>({ phase: "idle", file: null, previewUrl: null, error: null });
  const [apiError, setApiError] = useState<ApiError | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [itemSearch, setItemSearch] = useState("");
  const [items, setItems] = useState<ItemSummary[]>([]);
  const [showEvidence, setShowEvidence] = useState(false);
  const pollAbort = useRef<AbortController | null>(null);
  const polling = useRef(false);
  const uploadInFlight = useRef(false);
  const selectedReviewId = selectedReview?.review_id ?? null;

  const sections = useMemo(
    () => sortedReviewSections(reviewList ?? { reviews: [], total: 0, store_count: 0, store_capacity: 100 }),
    [reviewList]
  );

  const load = useCallback(
    async (options?: { forceForm?: boolean }) => {
      if (polling.current) {
        return;
      }
      polling.current = true;
      pollAbort.current?.abort();
      const controller = new AbortController();
      pollAbort.current = controller;
      try {
        const [nextCapabilities, nextList, nextSelected] = await Promise.all([
          capabilities ? Promise.resolve(capabilities) : getLocalRecognitionCapabilities(),
          getLocalRecognitionReviews(controller.signal),
          selectedReviewId ? getLocalRecognitionReview(selectedReviewId, controller.signal) : Promise.resolve(null)
        ]);
        setCapabilities(nextCapabilities);
        setReviewList(nextList);
        if (nextSelected) {
          setSelectedReview((current) => {
            const merged = mergeReviewsByPolling(current, nextSelected, dirty && !options?.forceForm);
            if (merged.conflict) {
              setConflictMessage("服务端状态已变化。请放弃本地编辑并重新载入后继续。");
            }
            return merged.review;
          });
          if (!dirty || options?.forceForm) {
            setForm(formFromReview(nextSelected));
            setDirty(false);
            setConflictMessage(null);
          }
        }
        setApiError(null);
      } catch (error) {
        if (!isAbortError(error)) {
          setApiError(toDisplayError(error));
        }
      } finally {
        polling.current = false;
      }
    },
    [capabilities, dirty, selectedReviewId]
  );

  useEffect(() => {
    void load({ forceForm: true });
    return () => pollAbort.current?.abort();
  }, [load]);

  useEffect(() => {
    let timeout: number | undefined;
    let delay = 2000;
    let stopped = false;

    async function tick() {
      if (stopped) {
        return;
      }
      if (document.visibilityState === "visible") {
        await load();
        delay = apiError ? Math.min(delay * 2, 15000) : 2000;
      } else {
        delay = 10000;
      }
      timeout = window.setTimeout(tick, delay);
    }

    timeout = window.setTimeout(tick, 2000);
    return () => {
      stopped = true;
      if (timeout) {
        window.clearTimeout(timeout);
      }
    };
  }, [apiError, load]);

  useEffect(() => {
    return () => {
      if (upload.previewUrl) {
        URL.revokeObjectURL(upload.previewUrl);
      }
    };
  }, [upload.previewUrl]);

  useEffect(() => {
    if (itemSearch.trim().length < 2) {
      setItems([]);
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      try {
        const result = await getItems({ search: itemSearch.trim(), page_size: "8" });
        if (!controller.signal.aborted) {
          setItems(result.items);
        }
      } catch {
        if (!controller.signal.aborted) {
          setItems([]);
        }
      }
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [itemSearch]);

  function selectFile(file: File | null) {
    const validation = validateScreenshotFile(file);
    if (upload.previewUrl) {
      URL.revokeObjectURL(upload.previewUrl);
    }
    if (!validation.ok || !file) {
      setUpload({
        phase: "error",
        file,
        previewUrl: null,
        error: { status: 0, code: validation.ok ? "missing_file" : validation.code, message: validation.ok ? "请选择文件。" : validation.message }
      });
      return;
    }
    setUpload({ phase: "selected", file, previewUrl: URL.createObjectURL(file), error: null });
  }

  async function uploadSelectedFile() {
    if (uploadInFlight.current || !upload.file) {
      return;
    }
    uploadInFlight.current = true;
    setUpload((current) => ({ ...current, phase: "uploading", error: null }));
    setActionMessage(null);
    try {
      const created = await uploadLocalRecognitionReview(upload.file);
      const detail = await getLocalRecognitionReview(created.review_id);
      setSelectedReview(detail);
      setForm(formFromReview(detail));
      setDirty(false);
      setActionMessage("已创建复核记录，后台 OCR 正在处理。");
      await load();
    } catch (error) {
      setUpload((current) => ({ ...current, phase: "error", error: toDisplayError(error) }));
    } finally {
      uploadInFlight.current = false;
    }
  }

  function selectReview(review: LocalRecognitionReview) {
    setSelectedReview(review);
    setForm(formFromReview(review));
    setDirty(false);
    setConflictMessage(null);
    setActionMessage(null);
  }

  function updateForm(values: Partial<ReviewFormState>) {
    setForm((current) => (current ? applyReviewFormUpdate(current, values) : current));
    setDirty(true);
  }

  async function saveDraft() {
    if (!selectedReview || !form) {
      return;
    }
    const validation = validateReviewForm(form);
    if (!validation.ok) {
      setActionMessage(validation.message);
      return;
    }
    try {
      const updated = await patchLocalRecognitionReview(selectedReview.review_id, payloadFromForm(form));
      setSelectedReview(updated);
      setForm(formFromReview(updated));
      setDirty(false);
      setActionMessage("草稿已保存。");
      await load();
    } catch (error) {
      setApiError(toDisplayError(error));
    }
  }

  async function confirmReview() {
    if (!selectedReview || !form) {
      return;
    }
    const validation = validateReviewForm(form);
    if (!validation.ok) {
      setActionMessage(validation.message);
      return;
    }
    try {
      const updated = await confirmLocalRecognitionReview(selectedReview.review_id, payloadFromForm(form));
      setSelectedReview(updated);
      setForm(formFromReview(updated));
      setDirty(false);
      setActionMessage("复核结果已确认，candidate JSON 已生成。");
      await load();
    } catch (error) {
      setApiError(toDisplayError(error));
    }
  }

  async function rejectCurrentReview() {
    if (!selectedReview || !form) {
      return;
    }
    try {
      const updated = await rejectLocalRecognitionReview(selectedReview.review_id, form.reviewerNote || null);
      setSelectedReview(updated);
      setDirty(false);
      setActionMessage("复核记录已拒绝。");
      await load();
    } catch (error) {
      setApiError(toDisplayError(error));
    }
  }

  async function markUnreadable() {
    if (!selectedReview || !form) {
      return;
    }
    try {
      const updated = await markLocalRecognitionReviewUnreadable(selectedReview.review_id, form.reviewerNote || null);
      setSelectedReview(updated);
      setDirty(false);
      setActionMessage("复核记录已标记为无法读取。");
      await load();
    } catch (error) {
      setApiError(toDisplayError(error));
    }
  }

  async function clearAllReviews() {
    if (!window.confirm("确认清空所有本地内存复核记录？服务端不会写数据库，但当前队列会被删除。")) {
      return;
    }
    try {
      const result = await clearLocalRecognitionReviews();
      setReviewList(null);
      setSelectedReview(null);
      setForm(null);
      setDirty(false);
      setActionMessage(`已清空 ${result.cleared} 条内存复核记录。`);
      await load();
    } catch (error) {
      setApiError(toDisplayError(error));
    }
  }

  const canEdit = selectedReview?.status === "pending_review" && form !== null;

  return (
    <div className="screen-review-layout">
      <aside className="panel screen-review-sidebar" aria-label="功能列表">
        {["实时识别", "待复核队列", "已确认记录", "布局配置", "OCR设置", "隐私与权限", "诊断"].map((label) => (
          <a key={label} href={`#${label}`} className="screen-review-nav-item">
            {label}
          </a>
        ))}
      </aside>

      <section className="screen-review-main">
        <UploadPanel
          upload={upload}
          onFile={selectFile}
          onUpload={uploadSelectedFile}
          onDrop={(event) => {
            event.preventDefault();
            selectFile(event.dataTransfer.files[0] ?? null);
          }}
        />

        <QueuePanel
          pending={sections.pending}
          finished={sections.finished}
          selectedId={selectedReview?.review_id ?? null}
          onSelect={selectReview}
        />

        <ReviewPanel
          canEdit={canEdit}
          conflictMessage={conflictMessage}
          dirty={dirty}
          form={form}
          itemSearch={itemSearch}
          items={items}
          onConfirm={confirmReview}
          onItemSearch={setItemSearch}
          onMarkUnreadable={markUnreadable}
          onReject={rejectCurrentReview}
          onReload={() => void load({ forceForm: true })}
          onSave={saveDraft}
          onShowEvidence={() => setShowEvidence((current) => !current)}
          onUpdate={updateForm}
          review={selectedReview}
          showEvidence={showEvidence}
        />

        {apiError ? (
          <section className="error-state" aria-live="polite">
            <h2>{apiError.code === "api_unreachable" ? "API 不可访问" : "请求失败"}</h2>
            <p>{apiError.message}</p>
          </section>
        ) : null}

        {actionMessage ? (
          <section className="panel compact-message" aria-live="polite">
            <p>{actionMessage}</p>
          </section>
        ) : null}
      </section>

      <aside className="screen-review-diagnostics">
        <DiagnosticsPanel
          capabilities={capabilities}
          reviewList={reviewList}
          selectedReview={selectedReview}
          onClearAll={clearAllReviews}
        />
      </aside>
    </div>
  );
}

function UploadPanel({
  onDrop,
  onFile,
  onUpload,
  upload
}: {
  onDrop: (event: DragEvent<HTMLLabelElement>) => void;
  onFile: (file: File | null) => void;
  onUpload: () => void;
  upload: UploadState;
}) {
  return (
    <section id="实时识别" className="panel screen-upload-panel" aria-labelledby="upload-heading">
      <div className="section-heading">
        <div>
          <h2 id="upload-heading">上传 current 截图</h2>
          <p>PNG/JPEG only，最大 {formatBytes(MAX_SCREENSHOT_BYTES)}。客户端预检查不替代后端验证。</p>
        </div>
        <button type="button" disabled={upload.phase !== "selected"} onClick={onUpload}>
          {upload.phase === "uploading" ? "上传中..." : "创建 Review"}
        </button>
      </div>
      <label
        className="screen-drop-zone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
      >
        <span>选择或拖放截图</span>
        <input
          type="file"
          accept=".png,.jpg,.jpeg,image/png,image/jpeg"
          onChange={(event: ChangeEvent<HTMLInputElement>) => onFile(event.currentTarget.files?.[0] ?? null)}
        />
      </label>
      {upload.file ? (
        <div className="detail-grid compact">
          <Info label="文件名" value={upload.file.name} />
          <Info label="文件大小" value={formatBytes(upload.file.size)} />
          <Info label="浏览器 MIME" value={upload.file.type || "未提供"} />
          <Info label="上传状态" value={upload.phase} />
        </div>
      ) : (
        <div className="empty-state">
          <h3>尚未选择截图</h3>
          <p>第一版只处理 current 市场页截图，不处理历史图、tooltip 或扩展自动截图。</p>
        </div>
      )}
      {upload.previewUrl ? <img className="screen-preview" src={upload.previewUrl} alt="待上传截图预览" /> : null}
      {upload.error ? (
        <div className="error-state compact-error">
          <h3>{upload.error.code}</h3>
          <p>{upload.error.message}</p>
        </div>
      ) : null}
    </section>
  );
}

function QueuePanel({
  finished,
  onSelect,
  pending,
  selectedId
}: {
  finished: LocalRecognitionReview[];
  onSelect: (review: LocalRecognitionReview) => void;
  pending: LocalRecognitionReview[];
  selectedId: string | null;
}) {
  return (
    <section id="待复核队列" className="panel" aria-labelledby="queue-heading">
      <div className="section-heading">
        <div>
          <h2 id="queue-heading">待复核队列</h2>
          <p>pending 优先，其次 unreadable/failed，再按创建时间显示。</p>
        </div>
      </div>
      <ReviewTable reviews={pending} selectedId={selectedId} onSelect={onSelect} empty="当前没有待复核记录。" />
      <h3 id="已确认记录">已确认记录</h3>
      <p className="muted-text">当前复核记录仅保存在本地服务内存中，服务重启后会清除。</p>
      <ReviewTable reviews={finished} selectedId={selectedId} onSelect={onSelect} empty="当前没有终态记录。" />
    </section>
  );
}

function ReviewTable({
  empty,
  onSelect,
  reviews,
  selectedId
}: {
  empty: string;
  onSelect: (review: LocalRecognitionReview) => void;
  reviews: LocalRecognitionReview[];
  selectedId: string | null;
}) {
  if (reviews.length === 0) {
    return (
      <div className="empty-state">
        <h3>{empty}</h3>
      </div>
    );
  }
  return (
    <div className="table-wrap compact-table">
      <table>
        <thead>
          <tr>
            <th>创建时间</th>
            <th>OCR 名称</th>
            <th>best bid</th>
            <th>best ask</th>
            <th>issues</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map((review) => (
            <tr key={review.review_id} className={selectedId === review.review_id ? "selected-row" : ""}>
              <td>
                <button className="plain-button table-action" type="button" onClick={() => onSelect(review)}>
                  {formatDateTime(review.created_at)}
                </button>
              </td>
              <td>{review.ocr_candidate.item_name_normalized ?? review.ocr_candidate.item_name_raw ?? "—"}</td>
              <td>{review.ocr_candidate.best_bid ?? "—"}</td>
              <td>{review.ocr_candidate.best_ask ?? "—"}</td>
              <td>{review.warnings.length}/{review.errors.length}</td>
              <td>{reviewStatusLabel(review.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewPanel({
  canEdit,
  conflictMessage,
  dirty,
  form,
  itemSearch,
  items,
  onConfirm,
  onItemSearch,
  onMarkUnreadable,
  onReject,
  onReload,
  onSave,
  onShowEvidence,
  onUpdate,
  review,
  showEvidence
}: {
  canEdit: boolean;
  conflictMessage: string | null;
  dirty: boolean;
  form: ReviewFormState | null;
  itemSearch: string;
  items: ItemSummary[];
  onConfirm: () => void;
  onItemSearch: (value: string) => void;
  onMarkUnreadable: () => void;
  onReject: () => void;
  onReload: () => void;
  onSave: () => void;
  onShowEvidence: () => void;
  onUpdate: (values: Partial<ReviewFormState>) => void;
  review: LocalRecognitionReview | null;
  showEvidence: boolean;
}) {
  if (!review || !form) {
    return (
      <section className="panel" aria-labelledby="review-heading">
        <h2 id="review-heading">识别与人工复核</h2>
        <div className="empty-state">
          <h3>选择或创建一个 Review</h3>
          <p>上传截图后会自动选择新记录；也可以从队列中打开已有记录。</p>
        </div>
      </section>
    );
  }

  const confirmValidation = validateReviewForm(form);

  return (
    <section className="panel review-editor-panel" aria-labelledby="review-heading">
      <div className="section-heading">
        <div>
          <h2 id="review-heading">识别与人工复核</h2>
          <p>
            {review.review_id} · {reviewStatusLabel(review.status)} · 识别置信度：不可用
          </p>
        </div>
        <button className="plain-button" type="button" onClick={onReload}>
          重新载入
        </button>
      </div>

      {conflictMessage ? (
        <div className="error-state compact-error">
          <h3>状态冲突</h3>
          <p>{conflictMessage}</p>
        </div>
      ) : null}

      <div className="detail-grid compact">
        <Info label="原始文件" value={review.image?.original_filename ?? "—"} />
        <Info label="图片尺寸" value={review.image ? `${review.image.width} x ${review.image.height}` : "—"} />
        <Info label="Layout" value={`${review.recognition.layout_name} ${review.recognition.layout_version}`} />
        <Info label="OCR backend" value={review.recognition.ocr_backend} />
      </div>

      <IdentityEditor
        canEdit={canEdit}
        form={form}
        itemSearch={itemSearch}
        items={items}
        onItemSearch={onItemSearch}
        onUpdate={onUpdate}
        review={review}
      />

      <ComparisonTable canEdit={canEdit} form={form} onUpdate={onUpdate} review={review} />

      <div className="issue-report">
        <IssueBlock title="Warnings" issues={review.warnings} />
        <IssueBlock title="Errors" issues={review.errors} />
      </div>

      <button className="plain-button" type="button" onClick={onShowEvidence}>
        {showEvidence ? "收起 evidence" : "展开 evidence"}
      </button>
      {showEvidence ? (
        <pre className="json-panel">{JSON.stringify(review.ocr_evidence_summary, null, 2)}</pre>
      ) : null}

      <label className="review-note">
        复核备注
        <textarea
          disabled={!canEdit}
          value={form.reviewerNote}
          onChange={(event) => onUpdate({ reviewerNote: event.currentTarget.value })}
        />
      </label>

      <div className="form-actions sticky-actions">
        <button type="button" disabled={!canEdit} onClick={onSave}>
          保存草稿
        </button>
        <button className="plain-button" type="button" disabled={!canEdit} onClick={onReject}>
          拒绝
        </button>
        <button className="plain-button" type="button" disabled={!canEdit} onClick={onMarkUnreadable}>
          标记无法读取
        </button>
        <button
          type="button"
          disabled={!canEdit || Boolean(conflictMessage) || !confirmValidation.ok}
          onClick={onConfirm}
        >
          确认结果
        </button>
        {dirty ? <span className="field-hint">有未保存修改</span> : null}
      </div>

      <CandidatePanel review={review} />
    </section>
  );
}

function IdentityEditor({
  canEdit,
  form,
  itemSearch,
  items,
  onItemSearch,
  onUpdate,
  review
}: {
  canEdit: boolean;
  form: ReviewFormState;
  itemSearch: string;
  items: ItemSummary[];
  onItemSearch: (value: string) => void;
  onUpdate: (values: Partial<ReviewFormState>) => void;
  review: LocalRecognitionReview;
}) {
  return (
    <section className="review-subsection" aria-labelledby="identity-heading">
      <h3 id="identity-heading">商品身份</h3>
      <div className="detail-grid compact">
        <Info label="OCR 原始名称" value={review.ocr_candidate.item_name_raw ?? "—"} />
        <Info label="OCR normalized 名称" value={review.ocr_candidate.item_name_normalized ?? "—"} />
        <Info label="OCR 建议名称" value={review.ocr_candidate.item_name_normalized ?? "未提供"} />
        <Info label="身份方式" value={form.identityMode === "existing" ? "已有 item" : "管理员手工身份"} />
        <Info label="是否修改名称" value={form.finalItemName !== (review.ocr_candidate.item_name_normalized ?? "") ? "是" : "否"} />
      </div>
      <div className="identity-grid">
        <label className="choice-pill">
          <input
            type="radio"
            checked={form.identityMode === "existing"}
            disabled={!canEdit}
            onChange={() => onUpdate({ identityMode: "existing", itemKey: "" })}
          />
          <span>从已有 items 选择</span>
        </label>
        <label className="choice-pill">
          <input
            type="radio"
            checked={form.identityMode === "manual"}
            disabled={!canEdit}
            onChange={() => onUpdate({ identityMode: "manual", selectedItemId: null })}
          />
          <span>填写管理员 item key</span>
        </label>
      </div>
      {form.identityMode === "existing" ? (
        <div className="identity-search">
          <label>
            搜索已有 item
            <input
              disabled={!canEdit}
              type="search"
              value={itemSearch}
              onChange={(event) => onItemSearch(event.currentTarget.value)}
              placeholder="输入至少 2 个字符"
            />
          </label>
          <div className="item-search-results">
            {items.map((item) => (
              <button
                className="plain-button"
                key={item.id}
                type="button"
                disabled={!canEdit}
                onClick={() =>
                  onUpdate({
                    selectedItemId: item.id,
                    finalItemName: item.name,
                    itemKey: ""
                  })
                }
              >
                {item.name} · {item.external_key}
              </button>
            ))}
          </div>
          <p className="field-hint">确认时后端会重新读取 item id、external key 和名称，不信任前端名称。</p>
        </div>
      ) : (
        <label>
          管理员 item key
          <input
            disabled={!canEdit}
            value={form.itemKey}
            onChange={(event) => onUpdate({ itemKey: event.currentTarget.value })}
            placeholder="administrator-provided-key"
          />
        </label>
      )}
    </section>
  );
}

function ComparisonTable({
  canEdit,
  form,
  onUpdate,
  review
}: {
  canEdit: boolean;
  form: ReviewFormState;
  onUpdate: (values: Partial<ReviewFormState>) => void;
  review: LocalRecognitionReview;
}) {
  const rows = [
    ["商品名称", review.ocr_candidate.item_name_normalized ?? review.ocr_candidate.item_name_raw ?? "—", "finalItemName", "必填"],
    ["Best bid", review.ocr_candidate.best_bid ?? "—", "finalBestBid", "必填且合法"],
    ["Best ask", review.ocr_candidate.best_ask ?? "—", "finalBestAsk", "必填且合法"],
    ["求购数量", formatQuantity(review.ocr_candidate.total_bid_quantity), "finalTotalBidQuantity", "可为空"],
    ["售单数量", formatQuantity(review.ocr_candidate.total_ask_quantity), "finalTotalAskQuantity", "可为空"]
  ] as const;
  return (
    <section className="review-subsection" aria-labelledby="comparison-heading">
      <h3 id="comparison-heading">OCR 值 / 人工最终值</h3>
      <p className="field-hint">数量表示截图中显示的商品数量，目前不会自动映射为 CSV 订单计数字段。</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>字段</th>
              <th>OCR识别</th>
              <th>人工最终值</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, ocr, key, status]) => {
              const value = String(form[key] ?? "");
              const edited = value !== "" && value !== String(ocr === "—" ? "" : ocr);
              return (
                <tr key={key} className={edited ? "edited-row" : ""}>
                  <th scope="row">{label}</th>
                  <td>{ocr}</td>
                  <td>
                    <input
                      disabled={!canEdit}
                      value={value}
                      onChange={(event) =>
                        onUpdate({ [key]: event.currentTarget.value } as Partial<ReviewFormState>)
                      }
                    />
                    <button
                      className="plain-button restore-button"
                      type="button"
                      disabled={!canEdit || ocr === "—"}
                      onClick={() =>
                        onUpdate({ [key]: ocr === "—" ? "" : String(ocr) } as Partial<ReviewFormState>)
                      }
                    >
                      恢复OCR值
                    </button>
                  </td>
                  <td>{status}</td>
                </tr>
              );
            })}
            <tr>
              <th scope="row">observed_at</th>
              <td>{formatDateTime(review.suggested_observed_at)}</td>
              <td>
                <input
                  disabled={!canEdit}
                  type="datetime-local"
                  value={form.observedAtLocal}
                  onChange={(event) => onUpdate({ observedAtLocal: event.currentTarget.value })}
                />
              </td>
              <td>{form.observedAtLocal ? "带时区提交" : "必填"}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CandidatePanel({ review }: { review: LocalRecognitionReview }) {
  const candidateText = review.candidate ? JSON.stringify(review.candidate, null, 2) : "";

  function copyCandidate() {
    if (candidateText) {
      void navigator.clipboard.writeText(candidateText);
    }
  }

  function downloadCandidate() {
    if (!candidateText) {
      return;
    }
    const url = URL.createObjectURL(new Blob([candidateText], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${review.review_id}.candidate.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="review-subsection" aria-labelledby="candidate-heading">
      <div className="section-heading compact-heading">
        <div>
          <h3 id="candidate-heading">Reviewed candidate JSON</h3>
          <p>candidate 尚未导入数据库，不等于正式 market snapshot。</p>
        </div>
        <div className="form-actions">
          <button className="plain-button" type="button" disabled={!review.candidate} onClick={copyCandidate}>
            复制JSON
          </button>
          <button className="plain-button" type="button" disabled={!review.candidate} onClick={downloadCandidate}>
            下载JSON
          </button>
        </div>
      </div>
      {review.candidate ? (
        <>
          <div className="detail-grid compact">
            <Info label="imported" value={String(review.candidate.imported)} />
            <Info label="database_written" value={String(review.candidate.database_written)} />
            <Info label="quantity semantics" value={review.candidate.quantity_semantics} />
            <Info label="CSV quantity mapping" value={review.candidate.csv_quantity_mapping} />
          </div>
          <pre className="json-panel">{candidateText}</pre>
        </>
      ) : (
        <div className="empty-state">
          <h3>尚未生成 candidate</h3>
          <p>确认结果后会在浏览器中显示、复制和下载单条 JSON。</p>
        </div>
      )}
    </section>
  );
}

function DiagnosticsPanel({
  capabilities,
  onClearAll,
  reviewList,
  selectedReview
}: {
  capabilities: LocalRecognitionCapabilities | null;
  onClearAll: () => void;
  reviewList: LocalRecognitionReviewList | null;
  selectedReview: LocalRecognitionReview | null;
}) {
  return (
    <div className="diagnostic-stack">
      <section className="panel" aria-labelledby="diagnostics-heading">
        <h2 id="diagnostics-heading">参数和诊断</h2>
        <div className="detail-grid compact">
          <Info label="API" value={capabilities ? "在线" : "待连接"} />
          <Info label="OCR backend" value={capabilities?.ocr_backend ?? "—"} />
          <Info label="Layout" value={capabilities ? `${capabilities.current_layout_profile} ${capabilities.layout_version}` : "—"} />
          <Info label="config hash" value={capabilities?.config_sha256.slice(0, 12) ?? "—"} />
          <Info label="store" value={reviewList ? `${reviewList.store_count}/${reviewList.store_capacity}` : "—"} />
          <Info label="TTL" value={capabilities ? `${capabilities.store_ttl_seconds / 3600} 小时` : "—"} />
        </div>
      </section>
      <section id="布局配置" className="panel">
        <h2>布局配置</h2>
        <p className="muted-text">第一版只读，不提供 ROI 编辑器。</p>
        <Info label="当前图片是否匹配" value={selectedReview?.status === "unreadable" ? "可能不匹配" : "由后端状态判断"} />
      </section>
      <section id="OCR设置" className="panel">
        <h2>OCR设置</h2>
        <div className="detail-grid compact">
          <Info label="backend" value={capabilities?.ocr_backend ?? "Windows OCR"} />
          <Info label="语言" value={capabilities?.installed_ocr_languages.join(", ") ?? "—"} />
          <Info label="debug artifacts" value="关闭" />
          <Info label="confidence" value="不可用" />
        </div>
      </section>
      <section id="隐私与权限" className="panel">
        <h2>隐私与权限</h2>
        <ul className="privacy-list">
          <li>图片只在本机处理，原图识别后删除。</li>
          <li>不访问 Gaijin Market，不读取 Cookie。</li>
          <li>不写数据库，不生成 CSV。</li>
          <li>浏览器扩展：未连接。</li>
          <li>自动识别：下一阶段提供。</li>
        </ul>
        <button className="plain-button" type="button" onClick={onClearAll}>
          清除全部内存Review
        </button>
      </section>
    </div>
  );
}

function IssueBlock({ issues, title }: { issues: string[]; title: string }) {
  return (
    <section className="issue-list">
      <h3>{title}</h3>
      {issues.length === 0 ? (
        <p className="muted-text">无 {title.toLowerCase()}。</p>
      ) : (
        <ul>
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </section>
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatQuantity(value: number | null): string {
  return value === null ? "—" : String(value);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

"use client";

import Link from "next/link";
import { useReducer, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { getImportJob, toDisplayError, uploadCsvImport } from "../../lib/api-client";
import type { ApiError, ImportErrorEntry, ImportJobResponse } from "../../lib/types";
import {
  MAX_CSV_UPLOAD_BYTES,
  abbreviateChecksum,
  formatFileSize,
  validateCsvFile
} from "./import-validation";
import { importPageReducer, initialImportPageState } from "./import-state";

const INITIAL_ISSUE_LIMIT = 20;

export function CsvImportForm() {
  const [state, dispatch] = useReducer(importPageReducer, initialImportPageState);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [issueLimit, setIssueLimit] = useState(INITIAL_ISSUE_LIMIT);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadInFlightRef = useRef(false);

  const isUploading = state.phase === "uploading";
  const canUpload = state.phase === "selected" && selectedFile !== null && !isUploading;

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0] ?? null;
    setSelectedFile(file);
    setIssueLimit(INITIAL_ISSUE_LIMIT);

    dispatch({
      type: "select_file",
      file: file ? { name: file.name, size: file.size, type: file.type } : null,
      validation: validateCsvFile(file)
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (uploadInFlightRef.current) {
      return;
    }

    const file = selectedFile;
    const validation = validateCsvFile(file);
    if (!validation.ok || !file) {
      dispatch({
        type: "select_file",
        file: file
          ? { name: file.name, size: file.size, type: file.type }
          : null,
        validation
      });
      return;
    }

    uploadInFlightRef.current = true;
    dispatch({ type: "start_upload" });

    try {
      const uploadResult = await uploadCsvImport(file);
      const detail = await getImportJob(uploadResult.job_id);
      dispatch({ type: "upload_success", result: detail });
    } catch (error) {
      dispatch({ type: "upload_error", error: toDisplayError(error) });
    } finally {
      uploadInFlightRef.current = false;
    }
  }

  function resetForm() {
    setSelectedFile(null);
    setIssueLimit(INITIAL_ISSUE_LIMIT);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
      fileInputRef.current.focus();
    }
    dispatch({ type: "reset" });
  }

  return (
    <>
      <section className="panel import-upload-panel" aria-labelledby="upload-heading">
        <div className="section-heading">
          <div>
            <h2 id="upload-heading">选择 CSV 文件</h2>
            <p>最大 {formatFileSize(MAX_CSV_UPLOAD_BYTES)}。不会读取或展示完整 CSV 内容。</p>
          </div>
          <Link className="button-link secondary-link" href="/items">
            查看商品
          </Link>
        </div>

        <form className="import-form" onSubmit={handleSubmit}>
          <label className="file-picker" htmlFor="csv-file">
            <span>CSV 文件</span>
            <input
              ref={fileInputRef}
              id="csv-file"
              name="file"
              type="file"
              accept=".csv,text/csv"
              disabled={isUploading}
              onChange={handleFileChange}
            />
          </label>

          <FileInfo file={state.file} />

          <div className="form-actions">
            <button type="submit" disabled={!canUpload}>
              {isUploading ? "上传中..." : "上传 CSV"}
            </button>
            <button className="plain-button" type="button" disabled={isUploading} onClick={resetForm}>
              再次导入
            </button>
          </div>
        </form>
      </section>

      <StatusPanel
        error={state.error}
        issueLimit={issueLimit}
        onShowMore={() => setIssueLimit((current) => current + INITIAL_ISSUE_LIMIT)}
        phase={state.phase}
        result={state.result}
      />
    </>
  );
}

function FileInfo({ file }: { file: { name: string; size: number; type: string } | null }) {
  if (!file) {
    return (
      <div className="file-info empty-state">
        <h3>尚未选择文件</h3>
        <p>请选择扩展名为 .csv 的文件。</p>
      </div>
    );
  }

  return (
    <div className="file-info detail-grid compact" aria-label="已选择文件信息">
      <InfoTile label="文件名" value={file.name} />
      <InfoTile label="文件大小" value={formatFileSize(file.size)} />
      <InfoTile label="浏览器 MIME" value={file.type || "未提供"} />
      <InfoTile label="选择状态" value="已选择，等待上传" />
    </div>
  );
}

function StatusPanel({
  error,
  issueLimit,
  onShowMore,
  phase,
  result
}: {
  error: ApiError | null;
  issueLimit: number;
  onShowMore: () => void;
  phase: string;
  result: ImportJobResponse | null;
}) {
  if (phase === "idle" || phase === "selected") {
    return null;
  }

  if (phase === "uploading") {
    return (
      <section className="panel" aria-live="polite" aria-labelledby="uploading-heading">
        <h2 id="uploading-heading">正在上传并导入</h2>
        <p className="muted-text">请保持页面打开。控件会在请求结束后恢复。</p>
      </section>
    );
  }

  if ((phase === "client_validation_error" || phase === "network_error") && error) {
    return (
      <section className="error-state" aria-live="assertive" aria-labelledby="upload-error-heading">
        <h2 id="upload-error-heading">
          {phase === "network_error" ? "服务不可用" : "文件未通过预检查"}
        </h2>
        <p>{error.message}</p>
      </section>
    );
  }

  if (!result) {
    return null;
  }

  if (phase === "duplicate") {
    return (
      <section className="panel result-panel" aria-live="polite" aria-labelledby="duplicate-heading">
        <h2 id="duplicate-heading">该文件此前已经成功导入</h2>
        <p className="muted-text">
          本次创建了 duplicate 导入记录，但不会暗示或执行数据再次写入。
        </p>
        <ImportSummary result={result} />
        <div className="form-actions">
          <Link className="button-link" href="/items">
            查看商品
          </Link>
        </div>
      </section>
    );
  }

  if (phase === "failed") {
    return (
      <section className="error-state result-panel" aria-live="assertive" aria-labelledby="failed-heading">
        <h2 id="failed-heading">导入失败</h2>
        <p>后端拒绝了该 CSV 或导入过程中出现可报告错误。请选择文件后重新上传。</p>
        <ImportSummary result={result} />
        <IssueReport result={result} issueLimit={issueLimit} onShowMore={onShowMore} />
      </section>
    );
  }

  return (
    <section className="panel result-panel" aria-live="polite" aria-labelledby="completed-heading">
      <div className="section-heading">
        <div>
          <h2 id="completed-heading">导入完成</h2>
          <p>数据已写入本地数据库，可由用户决定是否进入商品列表。</p>
        </div>
        <Link className="button-link" href="/items">
          查看商品
        </Link>
      </div>
      <ImportSummary result={result} />
      <IssueReport result={result} issueLimit={issueLimit} onShowMore={onShowMore} />
    </section>
  );
}

function ImportSummary({ result }: { result: ImportJobResponse }) {
  const warnings = result.error_report?.warnings.length ?? 0;
  const errors = result.error_report?.errors.length ?? 0;

  return (
    <div className="detail-grid import-summary" aria-label="导入结果摘要">
      <InfoTile label="job_id" value={String(result.job_id)} />
      <InfoTile label="filename" value={result.filename} />
      <InfoTile label="status" value={result.status} />
      <InfoTile label="row_count" value={String(result.row_count)} />
      <InfoTile label="valid_row_count" value={String(result.valid_row_count)} />
      <InfoTile label="invalid_row_count" value={String(result.invalid_row_count)} />
      <InfoTile label="checksum" value={abbreviateChecksum(result.checksum)} title={result.checksum} />
      <InfoTile label="duplicate_of_job_id" value={result.duplicate_of_job_id?.toString() ?? "无"} />
      <InfoTile label="warnings" value={String(warnings)} />
      <InfoTile label="errors" value={String(errors)} />
    </div>
  );
}

function IssueReport({
  issueLimit,
  onShowMore,
  result
}: {
  issueLimit: number;
  onShowMore: () => void;
  result: ImportJobResponse;
}) {
  const errors = result.error_report?.errors ?? [];
  const warnings = result.error_report?.warnings ?? [];

  return (
    <div className="issue-report">
      <IssueList
        heading="Errors"
        issues={errors}
        issueLimit={issueLimit}
        onShowMore={onShowMore}
      />
      <IssueList
        heading="Warnings"
        issues={warnings}
        issueLimit={issueLimit}
        onShowMore={onShowMore}
      />
    </div>
  );
}

function IssueList({
  heading,
  issueLimit,
  issues,
  onShowMore
}: {
  heading: string;
  issueLimit: number;
  issues: ImportErrorEntry[];
  onShowMore: () => void;
}) {
  const visibleIssues = issues.slice(0, issueLimit);
  const hasMore = issues.length > visibleIssues.length;

  return (
    <section className="issue-list" aria-labelledby={`${heading.toLowerCase()}-heading`}>
      <div className="section-heading compact-heading">
        <div>
          <h3 id={`${heading.toLowerCase()}-heading`}>{heading}</h3>
          <p>
            共 {issues.length} 条，默认展示前 {Math.min(issueLimit, issues.length)} 条。
          </p>
        </div>
      </div>

      {issues.length === 0 ? (
        <p className="muted-text">无 {heading.toLowerCase()}。</p>
      ) : (
        <div className="table-wrap">
          <table className="issue-table">
            <thead>
              <tr>
                <th>行号</th>
                <th>字段</th>
                <th>error_code</th>
                <th>message</th>
              </tr>
            </thead>
            <tbody>
              {visibleIssues.map((issue, index) => (
                <tr key={`${heading}-${issue.row_number ?? "file"}-${issue.field ?? "file"}-${issue.error_code}-${index}`}>
                  <td>{formatRowNumber(issue.row_number)}</td>
                  <td>{issue.field || "全局"}</td>
                  <td>{issue.error_code}</td>
                  <td>{issue.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasMore ? (
        <button className="plain-button" type="button" onClick={onShowMore}>
          显示更多
        </button>
      ) : null}
    </section>
  );
}

function InfoTile({ label, title, value }: { label: string; title?: string; value: string }) {
  return (
    <div className="info-tile" title={title}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatRowNumber(rowNumber: number | null): string {
  if (!rowNumber || rowNumber < 1) {
    return "文件";
  }

  return String(rowNumber);
}

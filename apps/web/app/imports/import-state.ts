import type { ApiError, ImportJobResponse } from "../../lib/types";
import type { ClientValidationResult } from "./import-validation";

export type ImportPagePhase =
  | "idle"
  | "selected"
  | "uploading"
  | "success"
  | "duplicate"
  | "failed"
  | "client_validation_error"
  | "network_error";

export type SelectedFileInfo = {
  name: string;
  size: number;
  type: string;
};

export type ImportPageState = {
  phase: ImportPagePhase;
  file: SelectedFileInfo | null;
  result: ImportJobResponse | null;
  error: ApiError | null;
};

export type ImportPageAction =
  | {
      type: "select_file";
      file: SelectedFileInfo | null;
      validation: ClientValidationResult;
    }
  | { type: "start_upload" }
  | { type: "upload_success"; result: ImportJobResponse }
  | { type: "upload_error"; error: ApiError }
  | { type: "reset" };

export const initialImportPageState: ImportPageState = {
  phase: "idle",
  file: null,
  result: null,
  error: null
};

export function importPageReducer(
  state: ImportPageState,
  action: ImportPageAction
): ImportPageState {
  if (action.type === "select_file") {
    if (!action.validation.ok) {
      return {
        phase: "client_validation_error",
        file: action.file,
        result: null,
        error: {
          status: 0,
          code: action.validation.code,
          message: action.validation.message
        }
      };
    }

    return {
      phase: "selected",
      file: action.file,
      result: null,
      error: null
    };
  }

  if (action.type === "start_upload") {
    if (state.phase === "uploading" || !state.file) {
      return state;
    }

    return {
      ...state,
      phase: "uploading",
      error: null
    };
  }

  if (action.type === "upload_success") {
    return {
      ...state,
      phase: phaseForImportStatus(action.result.status),
      result: action.result,
      error: null
    };
  }

  if (action.type === "upload_error") {
    return {
      ...state,
      phase: action.error.code === "api_unreachable" ? "network_error" : "failed",
      error: action.error
    };
  }

  return initialImportPageState;
}

export function phaseForImportStatus(status: ImportJobResponse["status"]): ImportPagePhase {
  if (status === "completed") {
    return "success";
  }

  if (status === "duplicate") {
    return "duplicate";
  }

  if (status === "failed") {
    return "failed";
  }

  return "uploading";
}

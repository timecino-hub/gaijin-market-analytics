"""Pure offline dataset adapters and inventory helpers."""

from gaijin_market_analytics.datasets.gaijin_history_json import (
    OFFLINE_MANIFEST_SCHEMA_VERSION,
    OfflineHistoryDataset,
    OfflineHistoryManifest,
    OfflineHistorySample,
    OfflineHistorySampleData,
    OfflineHistoryValidationError,
    load_offline_history_dataset,
    load_offline_history_manifest,
    source_manifest_sha256,
    typed_trade_dataset_sha256,
)
from gaijin_market_analytics.datasets.history_inventory import build_history_inventory

__all__ = [
    "OFFLINE_MANIFEST_SCHEMA_VERSION",
    "OfflineHistoryDataset",
    "OfflineHistoryManifest",
    "OfflineHistorySample",
    "OfflineHistorySampleData",
    "OfflineHistoryValidationError",
    "build_history_inventory",
    "load_offline_history_dataset",
    "load_offline_history_manifest",
    "source_manifest_sha256",
    "typed_trade_dataset_sha256",
]

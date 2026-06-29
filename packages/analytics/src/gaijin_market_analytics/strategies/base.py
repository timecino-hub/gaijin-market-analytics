from typing import Protocol

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult


class AnalysisStrategy(Protocol):
    strategy_name: str
    strategy_version: str
    feature_version: str

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        ...

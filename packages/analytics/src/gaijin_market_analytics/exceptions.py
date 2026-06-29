class AnalyticsError(ValueError):
    """Base class for stable analytics domain exceptions."""

    code = "analytics_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ContractValidationError(AnalyticsError):
    """Raised when an analysis input contract is invalid."""

    code = "contract_validation_error"


class InvalidDecimalError(ContractValidationError):
    code = "invalid_decimal"


class InvalidFeeRateError(ContractValidationError):
    code = "invalid_fee_rate"


class InvalidPriceError(ContractValidationError):
    code = "invalid_price"


class DuplicateStrategyError(AnalyticsError):
    code = "duplicate_strategy"


class StrategyNotFoundError(AnalyticsError):
    code = "strategy_not_found"

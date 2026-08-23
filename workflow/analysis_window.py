"""가격과 뉴스가 공유하는 최근 분석 기간 계약입니다."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_ANALYSIS_TIMEZONE = "America/New_York"


class AnalysisRequest(BaseModel):
    """사용자가 요청한 최근 구간 분석 조건입니다.

    ``analysis_days``는 ``as_of_date``를 포함하는 달력 일수입니다.
    ``as_of_date``가 없으면 실행 시점의 미국 동부 날짜를 사용합니다.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    ticker: str = Field(min_length=1)
    analysis_days: int = Field(gt=0)
    as_of_date: date | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Ticker의 공백을 제거하고 대문자로 정규화합니다."""
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be empty")
        return normalized


class AnalysisWindow(BaseModel):
    """가격과 뉴스 수집이 공통으로 사용할 확정 날짜 범위입니다."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    requested_analysis_days: int = Field(gt=0)
    start_date: date
    end_date: date
    timezone: str = DEFAULT_ANALYSIS_TIMEZONE

    @model_validator(mode="after")
    def validate_inclusive_day_count(self) -> "AnalysisWindow":
        """양 끝 날짜를 포함한 실제 일수가 요청 일수와 같은지 검증합니다."""
        inclusive_day_count = (self.end_date - self.start_date).days + 1
        if inclusive_day_count != self.requested_analysis_days:
            raise ValueError(
                "analysis window day count must match requested_analysis_days"
            )
        return self


def resolve_analysis_window(
    request: AnalysisRequest,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_ANALYSIS_TIMEZONE,
) -> AnalysisWindow:
    """요청을 양 끝 날짜가 포함되는 달력일 기준 분석 범위로 변환합니다.

    Args:
        request: Ticker, 분석 달력 일수, 선택적 기준일.
        now: 테스트에서 현재 시각을 고정하기 위한 timezone-aware datetime.
        timezone_name: 기준 시장 timezone. 첫 버전은 미국 동부 시간입니다.

    Raises:
        ValueError: 테스트용 ``now``가 timezone 정보 없이 전달된 경우.
    """
    timezone = ZoneInfo(timezone_name)

    if request.as_of_date is not None:
        end_date = request.as_of_date
    else:
        reference_time = now or datetime.now(timezone)
        if reference_time.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        end_date = reference_time.astimezone(timezone).date()

    start_date = end_date - timedelta(days=request.analysis_days - 1)

    return AnalysisWindow(
        ticker=request.ticker,
        requested_analysis_days=request.analysis_days,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone_name,
    )

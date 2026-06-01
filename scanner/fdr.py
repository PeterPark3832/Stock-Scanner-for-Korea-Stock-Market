"""FinanceDataReader 래퍼 — 지수 백오프 재시도."""
import time
try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None  # type: ignore[assignment]

from scanner.logger import log


def fdr_data_reader(ticker: str, start_date, retries: int = 3, delay: float = 1.0):
    if fdr is None:
        raise RuntimeError("FinanceDataReader 미설치")
    for attempt in range(1, retries + 1):
        try:
            return fdr.DataReader(ticker, start_date)
        except Exception as e:
            if attempt < retries:
                wait = delay * (2 ** (attempt - 1))
                log.warning(f"  [RETRY {attempt}/{retries}] {ticker}: {e} (재시도 {wait:.0f}초 후)")
                time.sleep(wait)
            else:
                raise

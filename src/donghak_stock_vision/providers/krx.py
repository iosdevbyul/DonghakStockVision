"""Official KRX daily trading information. No scraping or sample credentials."""

import json
from collections import OrderedDict
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from donghak_stock_vision.data.schema import DailyBar, validate_range, validate_ticker
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.providers.http import RetryingHTTP

ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd", "KONEX": "knx_bydd_trd"}


class KRXProvider:
    name = "krx"

    def __init__(self, api_key: str, http: RetryingHTTP, market: str = "KOSPI") -> None:
        if not api_key.strip():
            raise ValueError("KRX_API_KEY is required; obtain KRX key and API service approval")
        if market not in ENDPOINTS:
            raise ValueError("unsupported KRX market")
        self._api_key, self.http, self.market = api_key, http, market
        self._cache: OrderedDict[date, RawPage] = OrderedDict()

    def fetch(self, ticker: str, start: date, end: date) -> Iterator[RawPage]:
        validate_ticker(ticker)
        validate_range(start, end)
        minimum = date(2013, 7, 1) if self.market == "KONEX" else date(2010, 1, 4)
        if start < minimum:
            raise ProviderError(f"{self.market} history starts at {minimum}")
        current = start
        while current <= end:
            if current.weekday() < 5:
                if current in self._cache:
                    page = self._cache[current]
                    self._cache.move_to_end(current)
                else:
                    body = self.http.get(
                        f"https://data-dbg.krx.co.kr/svc/apis/sto/{ENDPOINTS[self.market]}",
                        headers={"AUTH_KEY": self._api_key},
                        params={"basDd": current.strftime("%Y%m%d")},
                    )
                    page = RawPage(body, datetime.now(UTC), current)
                    self._cache[current] = page
                    if len(self._cache) > 128:
                        self._cache.popitem(last=False)
                yield page
            current += timedelta(days=1)

    def parse(self, page: RawPage, ticker: str) -> list[DailyBar]:
        try:
            payload = json.loads(page.body)
            if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
                raise ValueError("missing OutBlock_1")
            bars = []
            for row in payload["OutBlock_1"]:
                if not isinstance(row, dict) or not isinstance(row.get("ISU_CD"), str):
                    raise ValueError("malformed KRX row")
                if row["ISU_CD"] != ticker:
                    continue
                trading_date = datetime.strptime(row["BAS_DD"], "%Y%m%d").date()
                if trading_date != page.source_date:
                    raise ValueError("KRX returned a different date")
                if row["MKT_NM"] != self.market:
                    raise ValueError("KRX returned a different market")
                bars.append(
                    DailyBar(
                        ticker=ticker,
                        trading_date=trading_date,
                        open=_integer(row["TDD_OPNPRC"]),
                        high=_integer(row["TDD_HGPRC"]),
                        low=_integer(row["TDD_LWPRC"]),
                        close=_integer(row["TDD_CLSPRC"]),
                        volume=_integer(row["ACC_TRDVOL"]),
                        trading_value=_integer(row["ACC_TRDVAL"]),
                        provider=self.name,
                        market=self.market,
                        adjustment="unadjusted",
                        collected_at=page.collected_at,
                    )
                )
            return bars
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            # Do not include server response / exception text: it may echo credentials.
            raise ProviderError("invalid KRX response or bar values") from error


def _integer(value: Any) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    if type(value) is int:
        return value
    raise ValueError("expected integer KRW/share value; missing values are not zero")

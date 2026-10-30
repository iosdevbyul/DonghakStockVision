# PAPER equity performance foundation

This is a pure, offline calculation module used to prepare future PAPER
performance reports. It does not create accounts, orders or fills; it does not
read KRX operational data or authorize any observed capture for trading.

Module: `donghak_stock_vision.trading.paper_performance`

Pass a strictly chronological sequence of positive daily equity valuations,
optionally with one complete benchmark index series. `summarize` returns:

- first and last observed trading dates
- initial/final equity and observation count
- total percentage return
- peak-to-trough maximum drawdown percentage
- optional benchmark percentage return and percentage-point excess return

The benchmark is an indexed value, not a monetary holding. All calculations
use `Decimal`, and empty, duplicated, out-of-order, nonpositive or partially
benchmarked input fails closed. The report is an observed-marks calculation,
not evidence of a legitimate PAPER signal.

## Integration prerequisites

The existing `forward_paper` broker reports fees/taxes and a marked equity
when a reference price is provided, but its account representation does not
provide reliable average entry cost or realized P&L. Do not infer either field
from marks. A future integration must establish independently reviewed
price provenance, timestamp alignment, fees, taxes and persistent valuation
history before exposing daily performance in the web app.

This change only establishes deterministic metric arithmetic and tests;
there is no live KRX request or active SQLite access.

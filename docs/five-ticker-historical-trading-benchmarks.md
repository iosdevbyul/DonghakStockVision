# Five-ticker bounded synthetic measurements

Environment: Python 3.12.14, macOS-26.7.1-x86_64-i386-64bit.

These are synthetic research/mechanics fixtures, not protected historical data or
investment-performance evidence. Genuine public model outputs and controlled
mechanics are measured separately. Timings are single observations on a shared
host while other tests run; they are not isolated throughput measurements.

| Signal path | Dates | Analyses | Orders | Fills | Frozen bytes | Detail bytes | Full result bytes | Replay s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| linear public inference | 2 | 10 | 0 | 0 | 1,632,175 | 93,801 | 1,979,366 | 21.982 |
| hgb public inference | 2 | 10 | 0 | 0 | 535,726 | 88,291 | 1,046,446 | 12.142 |
| linear mechanics/pending | 1 | 5 | 3 | 0 | 1,212,452 | 56,672 | 8,623,105 | 12.030 |

| linear mechanics/filled | 2 | 10 | 3 | 3 | 1,256,890 | 104,399 | 12,525,245 | 32.002 |

Each run has exactly five securities. All four frozen replays reproduced their
entire saved result exactly, including run/performance/report hashes. The JSON
companion records hashes and raw timing observations.

Public linear/HGB inference used the original fixture policy thresholds and
produced no orders in these observations. These outcomes were preserved.
The controlled pending example started with KRW 3,500, reserved KRW 3,000 for
three orders, and retained KRW 500 available; two later requests were denied
for insufficient cash. There was no later fill event in that bounded run.

Acquisition plus initial replay, excluding fixture training, took 23.643 s
for linear and 12.976 s for HGB. Offline replay times above exclude parsing the
outer saved result but include validating/reconstructing its frozen inputs.
No peak Python allocation, native memory or RSS was measured. In particular,
these small samples do not establish long trade-heavy memory/runtime limits.
Accepted receipts repeat shared source context; full journals can greatly exceed
the frozen input size. The integration tests separately exercise 20 fills and
10 completed position episodes with reentry.

Reproduction: generate isolated fixtures using `tests/backtest/test_portfolio.py`,
then run `python -m tests.backtest.portfolio_benchmark result.json --output metrics.json`.
The helper accepts at most three saved artifacts, performs no model acquisition,
and requires exact full-result replay. Timing data never enters canonical report
evidence or economic calculations.

The legacy eight-event single-ticker round trip with reentry was also executed
against an extracted copy of base main and the current tree. Run, Performance
and detailed-report hashes were identical:

```json
{"run_hash": "d38ea00d958c73527c4f66d64fefbd76919c5f0b6fbc03b1df2104af85523de3", "performance_hash": "5c2318e6f528e7a7a285e94a34ddbe7b7a63ad82c86bb7317efd7cfbabfa2ed3", "details_hash": "cf114b2fd3799644add4f915f02ce92096a14db21e5c5697fb4533302b42998b"}
```

[Contract and limitations](five-ticker-historical-trading.md).

The filled mechanics sample adds a second decision date: three orders fill,
three held tickers report HOLD and two unheld tickers report WAIT. The shared
account equity is KRW 3,467.54, unrealized P&L is -32.46, realized P&L is zero,
fees are 5.46 and recorded slippage impact is 27. Those are existing accounting
outputs from controlled test signals, not fitted-model profitability.

The separate complete mechanics integration result has 20 orders/fills, four
examined dates, 20 analyses, ten completed episodes and five reentries. Its full
canonical result is 84,618,984 bytes. Pure replay time and memory for that larger
result were not measured separately. Starting with KRW 1,000,000, its existing
Ledger/Performance outputs are final equity 1,001,705.5, realized net P&L 1,705.5,
fees 50.9, taxes 43.6 and slippage impact 200. All positions are closed by actual
fills; there is no synthetic terminal liquidation. These prescribed mechanics
signals establish no model performance claim.

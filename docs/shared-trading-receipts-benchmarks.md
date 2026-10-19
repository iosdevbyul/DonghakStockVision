# Compact receipt archive measurements

Python 3.12.14, macOS-26.7.1-x86_64-i386-64bit.

All four cases use five explicit tickers and controlled synthetic mechanics.
They establish transport/accounting equivalence, not fitted-model profitability.
Input files are isolated synthetic artifacts and were SHA-256 checked unchanged.
Each archive was fully expanded and replayed through original validators once.
The complete canonical result, decisions, receipts, Ledger, Position History,
Performance and detailed report (including all original hashes) matched exactly.

| Fills | Tickers | Decisions | Accepted | Expanded bytes | Compact bytes | Reduction | Artifacts | Detail bytes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 5 | 5 | 1 | 5,029,780 | 1,372,384 | 72.71% | 127 | 63,289 |
| 3 | 5 | 10 | 3 | 12,525,245 | 1,526,457 | 87.81% | 169 | 104,399 |
| 10 | 5 | 10 | 10 | 39,617,838 | 1,911,393 | 95.18% | 327 | 203,091 |
| 20 | 5 | 20 | 20 | 84,618,984 | 2,751,244 | 96.75% | 568 | 370,854 |

Times are seconds. Encoding includes graph construction, canonical transport
serialization and transport verification. JSON dump measures serialization of an
already built compact document. Decode includes FrozenJSON parsing/canonicalization;
cold verification is separate. Legacy text expansion joins verified stream output.
Validated expansion includes legacy materialization and the original full replay;
the replay column is measured inside that same call, not an additional engine run.

| Fills | Encode | JSON dump | Decode | Cold graph verify | Legacy text | Validated expansion | Original replay |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 16.671 | 0.021 | 0.048 | 0.509 | 0.102 | 15.537 | 14.996 |
| 3 | 4.981 | 0.026 | 0.057 | 1.204 | 0.258 | 38.343 | 36.903 |
| 10 | 13.984 | 0.024 | 0.064 | 3.555 | 0.859 | 204.699 | 200.161 |
| 20 | 28.816 | 0.034 | 0.094 | 9.223 | 1.730 | 845.917 | 835.589 |

Final encoding was measured separately after removing redundant original-ID parsing;
all observed transport hashes/bytes match the archives used for full validation.
The one-fill encoding time includes tracemalloc overhead and cannot be directly
compared with uninstrumented larger cases. Phase observations were made while
other local tests ran; timing differences are not engine speed claims.

| Fills | Original JSON dump | Original JSON decode | Compact decode + cold verification |
| ---: | ---: | ---: | ---: |
| 1 | 0.078 | 0.202 | 0.557 |
| 3 | 0.193 | 0.509 | 1.261 |
| 10 | 0.635 | 1.655 | 3.619 |
| 20 | 1.311 | 3.868 | 9.317 |

Cold archive reading **adds reference/digest verification** and is slower than
plain original JSON parsing in these observations. Constructing the graph also
costs substantially more than dumping an already built original JSON object.
The compact JSON dump itself is smaller/faster, but the trading engine and its
full evidence replay have not been optimized. Large replay remains expensive.

The one-fill Python encoding allocation peak was 42,969,861 bytes.
This includes encoding/graph verification and excludes pre-existing source objects,
file loading, native allocations, RSS and full engine replay. Larger peaks and
full replay allocation peaks are unmeasured. The source result already exists in
memory, so the writer and validated expansion are not bounded-memory engine claims.

Reproduce with `python -m tests.backtest.receipt_archive_benchmark <result.json>
--output <metrics.json>` (1–4 inputs). `--trace` is limited to inputs at most 8 MiB.
Inputs can be generated using `portfolio_helpers.prepared(controlled=True)` and
`mechanics_run`: one BUY date with cash 1500 for one fill, cash 3500 for three;
the first BUY/SELL cycle for ten, and both cycles for twenty. Keep each period
through its later fill date. Do not alter inferred scores after execution.
The public linear/HGB compatibility tests use genuine inference independently.

[Archive contract](shared-trading-receipts.md).

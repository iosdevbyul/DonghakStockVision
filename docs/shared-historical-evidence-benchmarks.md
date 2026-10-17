# Shared evidence synthetic measurements

These measurements use temporary synthetic fixtures, not protected databases or investment performance.
The executable procedure is `tests/backtest/shared_evidence_benchmark.py`. Timings are single wall-clock observations on the shared development host.

Environment: Python 3.12.14, Darwin x86_64; packages `{'scikit-learn': '1.9.1', 'numpy': '2.5.3', 'scipy': '1.18.1'}`.

Each corpus contains distinct genuine public inference signals. Both representations perform all original FrozenAnalysis checks.
The table below measures analysis evidence, not full trade execution. Full trading and detailed-report equality are separate regression tests.

| Family | Dates | Original bytes | Shared bytes | Shared artifacts | Original validation s | Shared validation s | Original Python peak bytes | Shared Python peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| linear | 1 | 847,524 | 728,707 | 6 | 0.1059 | 0.1354 | 10,759,260 | 11,005,888 |
| linear | 10 | 8,474,357 | 776,847 | 24 | 1.0928 | 0.7042 | 65,514,049 | 11,428,320 |
| linear | 50 | 42,370,543 | 989,953 | 104 | 5.5359 | 3.2363 | 308,860,121 | 13,295,578 |
| linear | 100 | 84,742,995 | 1,258,555 | 204 | 11.3246 | 6.2626 | not measured | not measured |
| hgb | 1 | 25,988 | 22,249 | 13 | 0.0054 | 0.0076 | 506,700 | 366,046 |
| hgb | 10 | 259,582 | 125,418 | 58 | 0.0476 | 0.0548 | 2,318,069 | 1,315,004 |
| hgb | 50 | 1,297,652 | 583,816 | 258 | 0.2801 | 0.3453 | 10,336,249 | 5,477,166 |
| hgb | 100 | 2,595,548 | 1,157,104 | 508 | 0.6605 | 0.8631 | not measured | not measured |

The JSON companion records serialization and deserialization times as well as every count and memory measurement.
Each linear corpus has 1 original model and 1 snapshot. Each HGB corpus has 1 pair reference / 2 distinct original model descriptors, plus a separate inference snapshot for every date. Original training snapshot references are retained.

The 100-date linear corpus shrank from 84,742,995 to 1,258,555 bytes; the HGB corpus shrank from 2,595,548 to 1,157,104 bytes.
This is a storage/memory tradeoff, not a claim that every operation is faster: HGB cold validation and encoding were slower, and single-date linear traced memory was slightly higher.
Memory at 100 dates is unmeasured because instrumentation is capped at 50. This is Python allocation peak, not RSS or native estimator memory.

Initial complete linear fixture: 5 decisions, 1 model, 1 snapshot; frozen request 4,291,364 bytes, detailed report 39,062 bytes.
The same request in shared form was 804,239 bytes with 14 artifacts; the detailed report remains exactly equal.
The initial fixture creation plus execution/report with tracemalloc took 124.20 s / 140,725,981 Python bytes. It includes imports and fixture training and must not be compared to evidence-only load peaks above.

Legacy-compatible output receipts still contain expanded evidence. The reduction above applies to frozen analysis input, not a complete long trading journal.
See [contract and limitations](shared-historical-evidence.md).

Complete five-decision replay, measured from fresh JSON loading through Runner, Ledger/history, Performance and detailed report (imports, fixture training and inference excluded):

| Family | Original replay s | Shared replay s | Original request bytes | Shared request bytes | Detailed report bytes (both) |
| --- | ---: | ---: | ---: | ---: | ---: |
| linear | 14.7578 | 15.3337 | 4,291,364 | 804,239 | 39,062 |
| hgb | 3.7539 | 4.0766 | 187,236 | 125,518 | 52,948 |

The complete run, performance and report values are exactly equal in each pair. These single wall measurements show input compression rather than an end-to-end replay speedup. Raw observations are in the JSON companion.

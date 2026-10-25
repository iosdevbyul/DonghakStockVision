# Forward market signals and local append-only journal

## Scope and integration

Base main `894e66f4075bad77f2217f78e0b901df3a199254` includes merged PR #141.
This feature calculates an executable observed-bar SMA crossover signal for KOSPI
`005930` in native KRW. A signal is a research decision, not an order, a position,
a Risk Engine approval, a recommendation or evidence of profitability.

Repository investigation found these reusable boundaries:

* Original PR #139 `ForwardCapturePlan`, split authorization, `run_capture`, raw
  response receipts, KRX parser and `verify_capture` provide the source owners.
* PR #140 custody staging/readback is local evidence linkage, not authenticated
  service permission, publication or independent custody. It remains unchanged.
* PR #141 descriptive historical analytics neither establishes decision-time
  availability nor supplies warm-up observations to this feature.
* Existing model-based `SignalService`, historical Decision Engine and synthetic
  price-band strategy have different inputs/semantics. They are unchanged. The
  small SMA reference is a separate pure strategy function, not another engine.
* Trading Core supplies the instrument identity. No Paper Broker command or Risk
  Engine call occurs because no order is proposed or executed in this PR.
* Existing SQLite journal patterns use atomic transactions and UPDATE/DELETE
  rejection triggers. The new signal table applies that pattern in an explicitly
  separate output file; it does not migrate or write to an original capture DB.
* `AnalysisStore` stores model/snapshot artifacts and adds execution audit rows on
  every `put`; it does not implement bounded head-pinned journal transitions.
  `DecisionStore` requires the existing model/account-based `DecisionInput` and
  authoritative `strategy.engine.decide` contract. Wrapping an SMA result in those
  contracts would misrepresent evidence. Both stores and their schemas remain
  unchanged; only their transaction/immutable-trigger patterns are reused.

Dependency direction is CLI -> storage adapter -> original capture verifier,
pure reference strategy and core contracts. Domain strategy code imports no
storage, HTTP, broker or CLI implementation. There are no architecture exceptions.

## Strategy and exact formulas

`forward-observed-sma-crossover`, strategy version `1`, includes the installed
strategy source SHA-256. Parameters are integer `1 <= fast < slow <= 30`.
At least `slow + 1` eligible observations are required, up to 32 explicitly
selected observations in strictly increasing trading-date order.

For the latest observed bar `t`, `SMA(n,t)` is the sum of the latest `n` recorded
closes divided by `n`. Previous means end at `t-1`.

* BUY: previous fast <= previous slow and current fast > current slow.
* SELL: previous fast >= previous slow and current fast < current slow.
* HOLD: neither crossing occurs, including equal current means.
* INSUFFICIENT_DATA: fewer than `slow+1` verified observations; no indicator values
  or actionable direction are fabricated.

Crossing comparisons use exact integer cross-multiplication. Indicators retain
numerator and denominator as well as Decimal means, rendered with precision 50,
half-even rounding and 18 fractional places. No floating-point comparison,
annualization, price adjustment, model inference or performance metric is used.
The means count **observed bars**, not independently verified consecutive exchange
sessions. A missing-session/calendar qualification is not implied.

The latest pair alone controls the signal. HOLD does not express a position-based
exit policy; BUY/SELL do not assert that an account can or should execute an order.

## Observation eligibility and original evidence

An explicit canonical `forward_signal_request_v1` supplies symbol, KOSPI/KRW,
mode, date window, strategy parameters, calculation time, eligibility policy,
latest capture age limit, original capture references and an independent pilot DB
snapshot SHA-256. The whole request also requires an independently retained pin.
The policy is `forward_local_capture_observed_bars_v1`.

The adapter:

1. Validates **every** original plan and its original split/pin before source I/O.
   Only original training/tuning plans are accepted. Final-test, final-holdout,
   OOS, mixed partitions/splits, duplicate dates and out-of-window sources fail.
2. Requires the original pilot DB, bounded to 256 MiB, with no active WAL/journal.
   It verifies the separately supplied snapshot hash before and after analysis.
3. Calls original `verify_capture` for each independently pinned manifest. This
   reconstructs the manifest from original raw bytes, receipts, request scope,
   parser and ingestion linkage. Exactly one successful parsed response, successful
   ingestion and no stop reason are required.
4. Uses the original captured bar, never the historical analytics DB or an
   automatically substituted latest cleaned bar. KRX/unadjusted semantics and
   original timestamps must agree.
5. Reads original `collection_runs.finished_at` as the **local ingestion completion
   audit time**. Manifest v1 does not pin this column; the independently supplied
   full DB snapshot pin binds it without modifying the old manifest contract.
6. Requires request start <= response completion <= ingestion completion <=
   calculation. Capture and ingestion times must be nondecreasing across the input
   window. Each completed daily bar must predate its request's Seoul calendar date.
   Latest capture age must be within the explicit 1–604800 second bound.

Trading date is a Seoul calendar date, not a UTC instant. Request start, local
retrieval/response completion, ingestion completion and signal calculation are
separate fields. All persisted instants are normalized to UTC `+00:00`.
Exchange publication, authenticated time and historical decision-safe availability
remain null. HTTP Date and local clocks are not promoted to publication evidence.

The caller must retain the original reviewed split, request, capture pins and
immutable DB snapshot independently. Hashes supplied only by a changed artifact
are not independent trust roots. Subsequent capture/custody writes change the DB
snapshot; retain the original snapshot for replay, rather than repinning old
journal decisions to new mutable data.

### Actual real-provider blocker

`authorized_provider` is recognized but currently fails closed with
`provider_authorization_not_independently_verified` **before opening the archive**.
Existing provider approval/terms strings and PR #140 operational assertions do not
independently authenticate service permission. No new approval authority or
permissive substitute is introduced. Synthetic inputs cannot be relabelled as
provider captures by changing request mode.

Consequently this PR completes the executable **offline synthetic** workflow;
it does not claim an authorized real-market forward signal was generated. A future
separately reviewed integration needs authentic scoped provider authorization,
permitted use/frequency evidence and reviewed local-capture eligibility. Historical
PIT approval and independent custody remain separate requirements.

## Complete runnable synthetic workflow

Run from an installed package or the repository development environment. Choose a
new capture-root child and a new journal; the demo refuses existing destinations.
It uses original `run_capture` with `httpx.MockTransport` and the original KRX parser.
There are zero live provider requests. Example for a POSIX shell:

```sh
workspace=$(mktemp -d /tmp/dsv-signals.XXXXXX)
dsv forward-signal offline-demo \
  --capture-root "$workspace/buy-capture" \
  --journal "$workspace/buy-signals.sqlite3" --outcome BUY > "$workspace/buy.json"
dsv forward-signal offline-demo \
  --capture-root "$workspace/sell-capture" \
  --journal "$workspace/sell-signals.sqlite3" --outcome SELL > "$workspace/sell.json"
dsv forward-signal offline-demo \
  --capture-root "$workspace/hold-capture" \
  --journal "$workspace/hold-signals.sqlite3" --outcome HOLD > "$workspace/hold.json"
```

Each demo prepares four synthetic `005930` daily bars dated 2025-07-01–04,
verifies their original receipts/manifests, calculates the signal, appends it,
reads it back, repeats idempotently, independently replays it and rejects a
self-rehashed forged direction. These dates/prices are fixtures, not real KRX
observations or historical forward decisions. Demo capture/ingestion/calculation
clocks record the actual local run; regeneration can have different artifact IDs.
For identical retained inputs and timestamps, outputs and fingerprints reproduce
exactly. Tests additionally use explicitly synthetic fixed clocks to verify
fresh-archive byte-for-byte request and decision reproduction.

Actual synthetic examples (`fast=2`, `slow=3`):

| Closes | Previous fast / slow | Current fast / slow | Signal |
|---|---|---|---|
| 100,90,80,120 | 170/2,270/3 | 200/2,290/3 | BUY |
| 100,110,120,80 | 230/2,330/3 | 200/2,310/3 | SELL |
| 100,100,100,100 | 200/2,300/3 | 200/2,300/3 | HOLD |

Inspect the generated request and operate the ordinary commands:

```sh
request_pin=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["request_pin"])' "$workspace/buy.json")
head_pin=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["journal_head_pin"])' "$workspace/buy.json")
signal_id=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["decision"]["signal_id"])' "$workspace/buy.json")
dsv forward-signal dry-run --capture-root "$workspace/buy-capture" \
  --request "$workspace/buy-capture/signal-request.json" --request-pin "$request_pin"
dsv forward-signal evaluate --capture-root "$workspace/buy-capture" \
  --request "$workspace/buy-capture/signal-request.json" --request-pin "$request_pin" \
  --journal "$workspace/buy-signals.sqlite3" --journal-pin "$head_pin"
dsv forward-signal history --journal "$workspace/buy-signals.sqlite3" \
  --journal-pin "$head_pin" --limit 10
dsv forward-signal verify --capture-root "$workspace/buy-capture" \
  --request "$workspace/buy-capture/signal-request.json" --request-pin "$request_pin" \
  --journal "$workspace/buy-signals.sqlite3" --journal-pin "$head_pin" --signal-id "$signal_id"
```

`evaluate` above returns `duplicate:true`; it retains the original insertion time
and head. `dry-run` reads sources only. `history` validates local chain integrity;
`verify` also recalculates the original decision using the original independently
pinned request, capture owners, raw bytes and snapshot. No request creates a client
except the explicitly synthetic demo's MockTransport client. Optional API,
learning, custody SDKs and credentials are not needed.

For a first append to a new journal, use the explicit genesis pin:

```sh
python -c 'from donghak_stock_vision.storage.forward_signal_journal import GENESIS; print(GENESIS)'
```

Supply this as `--journal-pin` to `evaluate`. New requests must explicitly declare
all parameters/mode/window/source pins in the request file. There are no implicit
symbol, warm-up DB, current-time decision or credential defaults. CLI errors exit 2.

## Decision and journal contracts

`forward_signal_decision_v1` contains signal ID, instrument, strategy/version/code
hash, parameter snapshot, UTC calculation time, latest trading date, ordered
observation IDs, source window and fingerprint, exact raw receipt/capture/plan/DB
references, original OHLCV, local timestamps, indicators, reason, origin and
explicit eligibility limitations. Its canonical `FrozenJSON.identifier` is the
decision fingerprint, returned separately as `decision_pin`.

Signal ID binds request pin and exact strategy reference. Recalculating a different
request, parameter or calculation instant creates a different signal. No
backfilled historical decision is represented as a live forward decision.

`forward_signal_entry_v1` adds sequence, previous entry pin, original decision pin
and a separate UTC journal insertion instant. The entry's canonical digest is the
new head pin. Insertion time does not change the underlying decision identity.
SQLite transactions commit a complete entry atomically; UPDATE/DELETE triggers,
unique signal ID/entry pins, strict chain sequencing and explicit expected-head
checks prevent silent overwrite, duplicate append and stale concurrent writers.
Same-ID/different-content is rejected. All old entries remain intact.

Limits: 32 input sources, 366-day window difference, 256 KiB request, 128 KiB entry,
256 retained decisions, 32 MiB journal. History returns at most 256 entries, default
50, after bounded chain validation. Capacity exhaustion refuses new appends; there
is no automatic destructive pruning. Provision a separate output journal under an
explicit operator retention plan. No authenticated custody, WORM or hard process
memory isolation is claimed.

A rollback before commit permits retry with the old head. A commit followed by a
lost response leaves one complete entry. Retrying with a stale head fails closed;
an operator must inspect the original local entry, verify its previous pin against
the last independently retained head and semantically replay its source-backed
decision before adopting the candidate new head. Never blindly trust a head found
inside a mutable journal. An interrupted empty-file initialization is refused,
not silently initialized over an unknown file. Resolve it explicitly at a new
journal path. Do not delete or replace existing evidence during recovery.

A malicious full rewrite or truncation cannot be detected by self-recomputed
hashes alone; the independently retained expected head is necessary. Local hash
chains and readback never establish external custody or an authenticated clock.

## Remaining boundaries

No real provider collection, external custody submission, Risk Engine bypass,
Paper Broker execution, automatic scheduler, API order route, strategy tuning,
profitability calculation or PIT/OOS/Corporate Actions approval occurs. Protected
historical data and final-test/holdout remain untouched. PR #143 signal-to-paper
execution is a separate milestone and is not implemented here.

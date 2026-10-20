# Local historical portfolio publication: operator runbook

This is a private, foreground, **synthetic research demonstration**. It does not
assert that a real-market historical archive exists. Do not use a public listener,
proxy or tunnel. Do not point these commands at protected data/approval directories.
Publication is read access only: not strict PIT/OOS qualification, investment
suitability, production AI approval or brokerage/trading authorization.

## What already existed and what this workflow adds

- `encode_result()` already builds compact receipts in Python. The existing
  `dsv-portfolio-publication` entry point now exposes it as `encode` with bounded
  flat-file I/O, immutable output and explicit **transport-only** status.
- Existing `publish` performs reference validation, exact expansion and original
  authoritative replay, verifies expected result/report hashes and explicitly
  registers the archive. This is also the full validation step: do not run another
  expensive replay just to proceed to publication.
- Existing `publish` prints `reportId` and `indexSha256`. Keep that command output
  outside the publication root as the operator's approved pin record. A hash read
  from a mutable index is not automatically approved.
- Existing `dsv-api` supplies the snapshot, publication root and trusted pin to the
  actual app; the new startup diagnostics hide upstream paths/stack traces.
- Existing four GET resources already provide the necessary client contract. No
  response schema, publication mechanism, authentication model or CI change is
  needed. `diagnose` adds bounded operator checks using that same pinned reader.

## Prerequisites and preparation

Use the repository checkout, Python 3.12+ and its existing virtual environment.
Install the project's documented extras in that environment if needed:

```sh
python -m pip install -e '.[api,learning,dev]'
```

The demo preparation module lives in `tests` and is deliberately **checkout-only**;
it is not a production model trainer or an installed wheel CLI. It reuses existing
verified synthetic fixtures. The publication/server commands are installed package
entry points. No credentials or live DART/KRX/broker access is needed.

From the repository root, choose a new canonical private output directory. This
example uses the standard library to make a private temporary parent and resolves
symlinks before passing it to the no-follow storage boundary:

```sh
DEMO_PARENT="$(python -c 'import pathlib,tempfile; print(pathlib.Path(tempfile.mkdtemp(prefix="dsv-portfolio-")).resolve())')"
DEMO="$DEMO_PARENT/demo"
python -m tests.api.portfolio_demo --output "$DEMO" --family mechanics > "$DEMO_PARENT/preparation.json"
PUB="$DEMO/publication"
```

The output must **not exist**; the tool never overwrites an old demo or source DB.
It creates private isolated synthetic Market/Analysis DBs under `sources`, a small
previously executed `publication/result.json`, a validated synthetic `snapshot.json`
and `demo.json` identifying its scope. It does **not** publish or start a server.
No generated archive or DB should be committed to Git.

The mechanics scenario has exactly five tickers (`000075`, `00088K`, `00104K`,
`005930`, `035420`), initial virtual cash `1500`, one shared account, one decision
date and a later eligible daily event. It guarantees at least one actual accepted
order/fill through the original runner. Its signals are labeled
`controlled_mechanics_test_double`, not fitted-model predictions or profitability.

Optional independent demonstrations reuse genuinely inferred fixtures:

```sh
python -m tests.api.portfolio_demo --output "$DEMO_PARENT/linear" --family linear
python -m tests.api.portfolio_demo --output "$DEMO_PARENT/hgb" --family hgb
```

These are still **synthetic data**, with tiny existing research training fixtures,
not real trading performance. Their accepted/fill counts may legitimately be zero.
HGB retains the original fixture input history needed for feature preparation;
the controlled/linear demo period can be two days. Each has one decision date.
Original unavailable HGB creation/attribution fields stay null. The tool refuses
to label a genuine-family demo successful when no public inference was produced.

For an existing authorized result instead, use a separate private canonical root
and stage its `five_ticker_historical_result_v1` JSON as `result.json` there. Use
original frozen result/report hashes for the following publication step. Do not
use the unrelated root-level legacy `backtest_portfolio.py` CSV strategy script.

## Encode and explicitly publish

```sh
dsv-portfolio-publication --root "$PUB" encode \
  --result result.json --archive input.json > "$DEMO/encoding.json"
RESULT_HASH="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["resultHash"])' "$DEMO/encoding.json")"
REPORT_HASH="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["reportHash"])' "$DEMO/encoding.json")"

dsv-portfolio-publication --root "$PUB" publish --archive input.json \
  --result-hash "$RESULT_HASH" --report-hash "$REPORT_HASH" > "$DEMO/operator-publication.txt"
REPORT_ID="$(sed -n 's/^reportId=//p' "$DEMO/operator-publication.txt")"
INDEX_SHA="$(sed -n 's/^indexSha256=//p' "$DEMO/operator-publication.txt")"
export DEMO PUB REPORT_ID INDEX_SHA

dsv-portfolio-publication --root "$PUB" --index-sha256 "$INDEX_SHA" \
  diagnose --report-id "$REPORT_ID"
```

`encode` prints JSON with original `resultHash`/`reportHash`, a distinct
`archiveTransportHash`, byte counts, `archiveValidation=transport_verified_only`
and `publicationAuthorized=false`. Encoding is not replay validation or publication.
No existing output with different bytes is overwritten.

`publish` uses the existing authoritative path, with no shortcut/cached approval.
It writes exactly the existing immutable manifest/read model/index contracts and
prints two `key=value` lines. For a previously initialized root, explicitly pass
its **current approved** `--index-sha256` before `publish`; do not omit it or adopt
an on-disk hash automatically. Repeated authorized publication is idempotent but
can still replay offline. Ordinary GET/diagnose/startup never replay.

For a non-demo result, compare the encoded result/report identities with the
original reviewed run's identities before authorizing publication. Do not treat
hashes computed from an arbitrary input file as independent research approval.

## Start the actual local API in the foreground

```sh
dsv-api --snapshot "$DEMO/snapshot.json" \
  --portfolio-publications "$PUB" --portfolio-index-sha256 "$INDEX_SHA" \
  --host 127.0.0.1 --port 8877
```

The snapshot above is explicitly labeled synthetic and contains no asserted global
model performance. For a real local workflow, supply the existing validated
Research API snapshot instead. Do not replace an original research snapshot with
the demo snapshot. An invalid/missing snapshot or root/pin fails closed.

`::1` is the other permitted bind (use `http://[::1]:8877` in curl). The serving CLI
disables proxy-header interpretation when this feature is configured. Client,
Host and Origin must remain loopback. The command stays in the foreground; **Ctrl-C
stops it**. No persistent background service is installed. If a port is occupied,
select another permitted port yourself; never kill an unrelated process.

## Exact bounded GET commands

In another terminal, supply the values saved from the operator command output
(the exported variables are available only in the original terminal):

```sh
BASE='http://127.0.0.1:8877/api/v1/research/historical-portfolios'
curl --fail --silent --show-error "$BASE?offset=0&limit=1"
curl --fail --silent --show-error "$BASE/$REPORT_ID"
curl --fail --silent --show-error "$BASE/$REPORT_ID/tickers/00088K?offset=0&limit=1"
curl --fail --silent --show-error "$BASE/$REPORT_ID/trades?offset=0&limit=2"
curl --fail --silent --show-error "$BASE/$REPORT_ID/trades?ticker=000075&offset=0&limit=1"
```

Before trusting a display, match list `reportId`, summary `resultHash`/`reportHash`
with the original run and publication output, and summary `archiveTransportHash`
with `encoding.json`. Ticker detail preserves exact ticker strings/model references;
trade rows retain stable opaque event/source IDs and original chronological order.
Use `offset`/`limit` (max 20) and preserve monetary strings/null reasons.

Expected controlled facts: five tickers, initial cash `1500`, accepted order count
`1`, fill count `1`, `researchOnly=true`, `operationalEligible=false`. The first
opportunity is BUY with a confirmed later fill; other insufficient-cash requests
remain rejected rather than being presented as fills. The report still labels the
input synthetic and the signal a controlled test double. Actual verified identities
and bounded GET observations are recorded in
[the executed example](historical-portfolio-workflow-example.json), not a hardcoded
archive fixture. IDs may differ on fresh fixture creation because original artifact
observation metadata is preserved. Replay of each frozen result is exact.

The summary exposes cash/equity/realized P&L/unrealized availability; ticker/trade
pages supply BUY/HOLD/SELL/WAIT or explicit unavailable outcomes, orders versus
fills, model provenance and quality limitations. Scores are conditional,
uncalibrated values. Daily-bar publication/fill simulation timestamps are not exact
exchange execution times. No client should substitute zero for unavailable valuation
or present feature contributions as proven causation. iOS/Web code is unchanged.

## Diagnostics and common errors

`diagnose` reads only the operator-pinned index and bounded approved read models.
It never opens a source Market/Analysis DB, loads a model or replays a run. It does
not refresh the pin from disk. Error output contains stable codes rather than
upstream filesystem paths or stack traces.

| Symptom/code | Operator action |
| --- | --- |
| `no_published_archive` | Valid empty/all-revoked index; list is correctly empty. Explicitly prepare/publish an authorized archive. |
| `missing_trusted_index_sha` / startup paired-option error | Supply the approved CLI output, not a newly guessed pin. |
| `archive_unavailable` | Verify canonical root, permissions, missing archive/projection and no symlink components. Investigate offline; no automatic repair. |
| `publication_manifest_mismatch` | Pin may be stale after publication/revocation, or manifest/projection corrupt. Review authorized offline output; restart with its reviewed pin. |
| `result_validation_required` | Corrupt compact graph, invalid result, provenance or hash mismatch; do not publish. |
| `unsupported_archive_version` | Only the existing five-ticker result/compact contracts are supported; do not relabel unknown schemas. |
| `archive_integrity_mismatch` | Published archive bytes changed. Stop access and investigate/revalidate offline, preserving original evidence. |
| `no_accepted_orders` / `no_fills` | Genuine possible outcome; inspect source reasons/timeline. Do not tune scores or invent fills to populate the API. |
| `valuation_evidence_unavailable`, `stale_mark`, `missing_mark` | Preserve null equity/return/unrealized values and recorded valuation reason. |
| `research_only_not_pit_oos_or_trading_authorization` | Expected restriction, never an operational approval. |
| `oversized_result`, `invalid_pagination` | Keep existing archive/projection/response limits and use a smaller valid page. |
| `local_research_access_required` | Use direct loopback; do not bypass via forwarded headers or public proxies. |
| `demo_output_exists` / `unsafe_demo_output` | Use a new private demo directory outside protected stores; old contents are not deleted. |

## Explicit revocation and shutdown

```sh
dsv-portfolio-publication --root "$PUB" --index-sha256 "$INDEX_SHA" \
  revoke --report-id "$REPORT_ID" > "$DEMO/operator-revocation.txt"
```

The existing server's old pin now fails closed with 503
`publication_manifest_mismatch`, including after earlier successful GETs. Stop it
with Ctrl-C, review the revocation output and explicitly set the new approved pin:

```sh
INDEX_SHA="$(sed -n 's/^indexSha256=//p' "$DEMO/operator-revocation.txt")"
dsv-portfolio-publication --root "$PUB" --index-sha256 "$INDEX_SHA" diagnose
dsv-api --snapshot "$DEMO/snapshot.json" --portfolio-publications "$PUB" \
  --portfolio-index-sha256 "$INDEX_SHA" --host 127.0.0.1 --port 8877
```

The collection is now valid and empty; the revoked ID returns 404
`report_unpublished`. Revocation does not automatically republish the archive or
kill/restart any process. Ctrl-C stops the final foreground server too. Keep or
manually remove only your own temporary demo directory when finished.

## Validation and bounds

`tests/api/test_portfolio_workflow.py` executes preparation, real encode/publication
CLI functions, one authoritative replay, actual serving CLI/app initialization and
real TestClient GETs for mechanics, linear and HGB. Uvicorn's socket runner is
captured so the test opens no public listener or permanent service; the actual app
and pin/startup configuration are used. A second heavy live HTTP fixture is not
needed. GET/diagnose/startup forbid replay, inference and model loading in tests.
Byte fingerprints cover source Market/Analysis DBs, compact/expanded input and
publication files. Tests also check unregistered valid transport, stale pin after
revocation, modified archive, missing projection and non-loopback rejection.

All existing byte/depth/reference/projection/index/page/response bounds remain.
Preparation is fixed to five synthetic tickers, one decision date and the bounded
fixture history; there is no arbitrary market acquisition/date/count option.
No large archive is committed. Existing CI, triggers, matrix and coverage policies
are unchanged; live tests remain excluded from the single full offline regression.

## Multiple publications → comparison → revocation

The [comparison contract](research-historical-portfolios.md#read-only-portfolio-comparison)
defines fields and restrictions. This section connects the existing commands; it
adds no publication system. Run these blocks from the installed checkout. All
outputs below belong to a **new private synthetic workspace**, not protected DBs
or a real-market historical archive. Stop on any failed preparation/encode/publish.

### Prepare the explicitly selected synthetic results

```sh
CMP="$(python -c 'import pathlib,tempfile; print(pathlib.Path(tempfile.mkdtemp(prefix="dsv-comparison-")).resolve())')"
export CMP
python -m tests.api.portfolio_demo --output "$CMP/mechanics" --family mechanics
python -m tests.api.portfolio_demo --output "$CMP/linear" --family linear
python -m tests.api.portfolio_demo --output "$CMP/hgb" --family hgb
python -m tests.api.portfolio_demo --output "$CMP/stale" --family mechanics --scenario stale_valuation
mkdir -m 700 "$CMP/publications" "$CMP/approved-output"
```

The first three use the original fixed five-ticker fixtures. The optional fourth
uses the **existing** controlled stale-mark fixture: its bounded original range,
omitted final bar, one decision date and strict mark age. It is only supported for
`--family mechanics`. It does not delete real market rows or change any original
valuation policy. The helper refuses this scenario for genuine model families
before creating output. Mechanics must really fill; stale valuation must really be
unavailable. No fabricated API result is inserted. All four remain synthetic;
genuine Linear/HGB inference is distinct from the controlled mechanics test double.

### Encode and register using the current approved CLI output

This explicit list is not a directory scan or automatic approval of arbitrary
archives. Run it only after choosing to publish these four synthetic results.
The inner shell stops on the first error; keep the per-publication command output
outside the publication root. No file is sourced or evaluated as shell code.

```sh
bash -e <<'SH'
PUB="$CMP/publications"
CURRENT_PIN=''
for LABEL in mechanics linear hgb stale; do
  cp "$CMP/$LABEL/publication/result.json" "$PUB/$LABEL-result.json"
  dsv-portfolio-publication --root "$PUB" encode \
    --result "$LABEL-result.json" --archive "$LABEL-input.json" \
    > "$CMP/approved-output/$LABEL-encoding.json"
  python - "$CMP/$LABEL/demo.json" "$CMP/approved-output/$LABEL-encoding.json" <<'PY'
import json, sys
facts, encoded = [json.load(open(path)) for path in sys.argv[1:]]
assert all(facts[key] == encoded[key] for key in ('resultHash', 'reportHash'))
assert encoded['publicationAuthorized'] is False
PY
  RESULT_HASH="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["resultHash"])' "$CMP/approved-output/$LABEL-encoding.json")"
  REPORT_HASH="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["reportHash"])' "$CMP/approved-output/$LABEL-encoding.json")"
  if [ -z "$CURRENT_PIN" ]; then
    dsv-portfolio-publication --root "$PUB" publish --archive "$LABEL-input.json" \
      --result-hash "$RESULT_HASH" --report-hash "$REPORT_HASH"
  else
    dsv-portfolio-publication --root "$PUB" --index-sha256 "$CURRENT_PIN" publish \
      --archive "$LABEL-input.json" --result-hash "$RESULT_HASH" --report-hash "$REPORT_HASH"
  fi > "$CMP/approved-output/$LABEL-publication.txt"
  cat "$CMP/approved-output/$LABEL-publication.txt"
  CURRENT_PIN="$(sed -n 's/^indexSha256=//p' "$CMP/approved-output/$LABEL-publication.txt")"
done
SH
```

Review the emitted records and retain them. Each expected result/report hash comes
from the original prepared result and is checked against encoding output. Existing
`publish` performs authoritative replay once per registration. Each subsequent
registration uses the preceding authorized command's pin; never hash the mutable
index and silently adopt that value as approval. Do not repeat full replay on GET
or comparison. The new server must use the **last** approved pin, not the first.

```sh
PUB="$CMP/publications"
CMP_INDEX="$(sed -n 's/^indexSha256=//p' "$CMP/approved-output/stale-publication.txt")"
REPORT_HGB="$(sed -n 's/^reportId=//p' "$CMP/approved-output/hgb-publication.txt")"
REPORT_MECHANICS="$(sed -n 's/^reportId=//p' "$CMP/approved-output/mechanics-publication.txt")"
REPORT_LINEAR="$(sed -n 's/^reportId=//p' "$CMP/approved-output/linear-publication.txt")"
REPORT_STALE="$(sed -n 's/^reportId=//p' "$CMP/approved-output/stale-publication.txt")"
export PUB CMP_INDEX REPORT_HGB REPORT_MECHANICS REPORT_LINEAR REPORT_STALE

dsv-portfolio-publication --root "$PUB" --index-sha256 "$CMP_INDEX" \
  diagnose --report-id "$REPORT_STALE"
python -c 'import json,sys; print(json.dumps({"reportIds":sys.argv[1:]}))' \
  "$REPORT_HGB" "$REPORT_MECHANICS" "$REPORT_LINEAR" "$REPORT_STALE" \
  > "$CMP/approved-output/comparison-request.json"
dsv-api --snapshot "$CMP/mechanics/snapshot.json" --portfolio-publications "$PUB" \
  --portfolio-index-sha256 "$CMP_INDEX" --host 127.0.0.1 --port 8877
```

That foreground command uses the actual existing serving CLI. In another terminal,
restore `CMP`/`PUB` from your own workspace and authorized output; do not copy an
example's IDs or pin. Query the running app:

```sh
curl --fail-with-body -sS http://127.0.0.1:8877/api/v1/research/historical-portfolios/compare \
  -H 'Content-Type: application/json' \
  --data-binary "@$CMP/approved-output/comparison-request.json" \
  > "$CMP/approved-output/comparison-response.json"
```

Check `items` against the request order (HGB, mechanics, linear, stale), original
result/report/run/performance hashes, model references and `provenance`. Every item
must state integrity verification, prior offline authoritative replay and research
restrictions. The executed fixture has one accepted/filled controlled mechanics
order; genuine Linear/HGB have zero orders/fills. Preserve that distinction rather
than manufacturing a successful investment outcome. The stale item has null equity
and unrealized P&L with original `stale_mark` evidence. Periods and valuation
observation times differ; the complete valuation policy is unavailable in the
existing projection. No period normalization, imputation, ranking or recommendation
is supported. The existing GET summary/ticker/trades commands above remain useful
for decision/fill references. CORS still allows GET only: browser cross-origin POST
preflight is blocked; the example uses a direct local client.

### Verified diagnostics and explicit revocation

The [executed example](historical-portfolio-workflow-example.json) includes an
independent `comparisonWorkflow` run with real observed IDs, provenance, request,
response and diagnostics. Its IDs/pin belong only to that temporary run. Fresh
preparation may change original observation metadata and identities. Never apply
those IDs to another workspace, and never label these as actual market returns.

| Situation | Observed HTTP / existing code | Operator interpretation |
| --- | --- | --- |
| Unapproved ID or valid but unregistered archive | 404 `report_not_found` | Files alone do not grant publication. |
| Malformed ID, duplicate, 6 IDs, path ID/root selector | 422 `invalid_comparison_request` | Supply only 2–5 unique approved IDs. |
| Changed archive | 503 `archive_integrity_mismatch` | Fail the entire request; investigate offline. |
| Missing projection | 503 `archive_unavailable` | Fail closed; HTTP never repairs evidence. |
| Stale pin after revocation | 503 `publication_manifest_mismatch` | Review the CLI output and explicitly restart. |
| Revoked ID with new approved pin | 404 `report_unpublished` | Revocation is enforced; do not substitute another run. |
| Stale valuation | 200, null values and `valuation_evidence_unavailable` / `stale_mark` | Availability limitation, not a failed publication. |
| All publications revoked | list 200, `total=0`; diagnose `no_published_archive` | Valid empty active registry; comparison of known revoked IDs is 404 `report_unpublished`, never-registered IDs are 404 `report_not_found`. |
| Invalid local Host/Origin | 403 `local_research_access_required` | Keep the original loopback boundary. |

These are existing contracts, not new errors. The tests damage only isolated
publication copies; do not mutate or remove your original archives to reproduce
errors. The difference between empty-list success and comparison's 404 is
intentional: a comparison still requires 2–5 active registered identities.

For explicit HGB revocation, use your **current approved** pin:

```sh
dsv-portfolio-publication --root "$PUB" --index-sha256 "$CMP_INDEX" \
  revoke --report-id "$REPORT_HGB" > "$CMP/approved-output/hgb-revocation.txt"
```

The old server now rejects comparisons with 503. Stop it using Ctrl-C, review the
revocation output, and explicitly replace the configuration pin:

```sh
CMP_INDEX="$(sed -n 's/^indexSha256=//p' "$CMP/approved-output/hgb-revocation.txt")"
dsv-api --snapshot "$CMP/mechanics/snapshot.json" --portfolio-publications "$PUB" \
  --portfolio-index-sha256 "$CMP_INDEX" --host 127.0.0.1 --port 8877
```

The original request now returns 404 `report_unpublished`; a request containing
only the other three active IDs succeeds. Revoke any remaining IDs only through
the same explicit CLI, each time carrying forward its new authorized pin. After
all four are revoked, an explicitly reconfigured server lists zero active reports.
No automatic pin refresh, republish, process kill or background daemon is added.
Ctrl-C stops the foreground server when finished.

### Integrity and automated validation scope

`tests/api/test_portfolio_comparison_workflow.py` uses the real preparation parser,
encode/publish/diagnose/revoke CLI functions and serving CLI to build the actual
FastAPI app. Only Uvicorn's socket runner is captured; HTTP uses TestClient, with
no listener left running. It executes four original synthetic results and exactly
four authoritative publication replays, then forbids replay/backtest, Linear/HGB
model construction/loading and inference. Socket connections are forbidden during
both generation and reads. All ordinary API requests must leave these counters at
zero. This does not claim that a real TCP server or frontend was tested.

SHA-256 fingerprints cover every prepared source DB/model fixture and expanded
result, staged compact input, published archive, projection and manifest. Base
publication/source bytes remain unchanged after comparisons and diagnostics.
Corruption/revocation tests use disposable copies; only authorized revocation may
change their index/lock state. The committed example records observed source DB
and publication-file hashes; large DB/archive/model objects are never committed.

Keep the existing [single-publication validation and bounds](#validation-and-bounds).
The stale scenario is bounded by the original five-ticker fixture's date range,
not an arbitrary live-data acquisition option. The complete offline suite belongs
to the user-managed Self-hosted CI; this workflow does not change its runner,
Python version, test scope or execution policy. No protected DB, original evidence,
qualification, approval, Operations state, iOS or Web project is modified.

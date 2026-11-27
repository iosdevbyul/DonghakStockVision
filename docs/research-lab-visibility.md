# Research Lab R7 · TrisMarketLens integration

R7 exposes approved R1–R6 evidence from a private, immutable Research Lab publication. With an R7 publication loaded, the server serves the machine-readable contract at `GET /openapi.json` and Swagger UI at `GET /docs`. The published JSON carries the original experiment and model IDs. Join on the full IDs. R6 window IDs are the original run IDs. R1–R4 use the original archived audit/result IDs, and the R5 registration view has a derived listing ID plus original experiment/model IDs.

## HTTP contract

Use `GET` for every row below and set `Authorization: Bearer <reader-token>`. The credential needs `research:read`. The process binds to loopback. Host and browser Origin must be local. Keep the token with the authorized local client or a server-side client component. For a browser, the default CORS origins are `http://localhost:3000` and `http://127.0.0.1:3000`.

All paths in the table are relative to `/api/v1/research/lab`. The schema names match the generated OpenAPI components. Query parameters are optional unless specified. IDs are 64 lowercase hexadecimal characters and tickers are six-digit strings, including leading zeroes. Horizons are integer values `1`, `5`, `20`.

| Path | Query parameters besides pagination | Response model and UI |
| --- | --- | --- |
| `/overview` | — | `Overview` for phase statuses, fit/cached work, evaluated model–rows, prospective counts, evidence qualification and latest observation date |
| `/runs` | `stage` | `Page[Run]` for the run list and recorded resources |
| `/runs/{run_id}` | Required path `run_id` | `Run` for observed timing, windows, resource costs, recoveries, stop reason and R4 strategy arms |
| `/experiments` | `stage`, `runId`, `experimentId`, `modelId`, `horizon` | `Page[Experiment]` for the model list, training rows, family, mask and hyperparameters |
| `/evaluations` | Experiment filters plus `exposure`, `evidenceStatus`, `populationId` | `Page[Evaluation]` for thresholds/contract, denominators, model and baseline metrics, block evidence, and run order |
| `/prediction-rows` | Experiment filters plus `evaluationId`, `exposure`, `ticker`, `correct`, `state` | `Page[PredictionItem]` for dates, prices, scores, actual labels and correctness or exclusions |
| `/evaluation-comparisons` | `stage`, `runId`, `evaluationId`, `horizon`, `populationId` | `Page[Comparison]` for matched baseline metrics, deltas and paired correctness |
| `/decisions` | `stage`, `runId`, `horizon`, `feedbackId` | `Page[Decision]` for research order, candidate selection and counterfactual history |
| `/failure-feedback` | `feedbackId` | `Page[Feedback]` for original failure cohorts and linked follow-up experiments |
| `/prospective` | — | `Prospective` for registered models, input readiness, counts, anchors and blocking reasons |
| `/krx-status` | — | `Operations` for the last verified capture, stored observations, missing sessions, local quota and last observed agent state |

`stage` is `R1` through `R6`. `exposure` is `historical_readout`, `development`, `exposed_fixed`, or `prospective`. `state` is `evaluated`, `neutral`, `excluded`, `missing`, `pending`, or `unavailable`. `correct` is a boolean. `evidenceStatus` accepts the exact case-sensitive evidence code returned by the API.

Only `Page[...]` routes accept pagination. `offset` is an integer from 0 to 100,000, default 0. `limit` is 1–100, default 25. `sort` is `asc` or `desc`, default `asc`. Ascending prediction order is input date, ticker, evaluation ID and prediction ID. Runs use stage, recorded start time and ID. Run-linked list rows use run order, recorded order when available, then ID. Follow `nextOffset` until it is `null` and reset pagination if `publishedAt` changes. `total` is the count after filters.

| Status | Client action |
| --- | --- |
| 200 | Decode the model. An empty page has `items: []`. |
| 401 | Supply a valid bearer credential. `{"detail":{"code":"research_authentication_required"}}` |
| 403 | Check the read scope or loopback Host/Origin. Codes are `research_read_scope_required` and `local_research_access_required`. |
| 404 | The run is missing from this publication, or the R7 routes are unavailable on this process. A missing run returns `{"detail":{"code":"research_lab_item_not_found"}}`. |
| 405 | Use GET. |
| 413 | Shorten the URL and filters. Paths and query strings are bounded to 4,096 combined bytes/characters. |
| 422 | Correct the field identified in FastAPI’s `detail` array. |

## Display rules

- **Overview.** Read `publishedAt` alongside `lastEndedAt` and `executionStatus`. These are retained results and the snapshot’s execution status. The 58 fits include five verified R2 recovery fits. `fitsRecovered` and `calibrationFitsRecovered` are subsets. Show `referenceOnly` for R4’s retained fixed-panel comparison models. Repeated evaluation of the same observation is reflected in the model–row denominator.
- **Model research.** Fetch `/evaluations` with the selected experiment and run. R2 calibration has an original `transformId` on the evaluation. Training and evaluation populations have safe identifiers, with date spans and ticker counts when available. Keep developmental, fixed exposed and independent evidence in their stated contexts.
- **Predictions.** Use the retained probability and labels. Class 1 is up at the original 0.5 decision threshold and class 0 is down. The direction label follows the delayed-close contract using the next reference close and the retained horizon-specific target close, with the existing 20 bps neutral band. Price is in KRW and return is a decimal ratio. For historical rows the original generation timestamp is unavailable. When row-level exclusions exist, show their `reason`. Aggregate-only exclusions stay on the evaluation denominator. Null always means unavailable, undefined or inapplicable for that item.
- **Comparison and progress.** Join `/evaluations` and `/evaluation-comparisons` on `evaluationId`. Group series by `populationId`, horizon and contract. Deltas use candidate minus baseline, including Brier and false-positive rate. Lower Brier and FPR are favorable. The four paired transition bins sum to the evaluated denominator. A null `transitions` means the verified baseline is aggregate-only. `NOT_COMPARABLE` has no interpretable delta. `ABSTAIN` or `INSUFFICIENT_EVIDENCE` leaves Legacy in place even when a headline development metric rises. Follow `/decisions` order to graph an eligible series. A changed candidate identifies a different selection from the counterfactual, with no implication of independent improvement.
- **Future validation.** Read R5 status with `/prospective`. The existing #250 Shadow read endpoints under `/api/v1/research/predictions` provide separately pinned prediction/outcome rows when configured. Use their prospective and replay mode filters. The R7 historical row list will yield an empty page for `exposure=prospective`. Pending legacy forecasts remain pending until a recorded resolve even when their price window is available. A descriptive H20 registration and zero independent anchors do not establish forecasting performance.
- **Operations.** Show `checkedAt` and `latestTradingDate` separately. The most recent receipt attached to this snapshot has its own `lastCaptureTradingDate` and `requestedAt`. `enabled`, `agentState`, and `policyValid` must be rendered separately. The observed state was enabled, agent idle, authorization expired. Local request quota and provider quota are distinct, with provider quota unavailable. Missing trading dates come from the verified calendar as of snapshot creation and its `calendarEnd`. Read `/prospective` for R5 observation readiness.

Study URLs under this prefix, including the existing `/comparisons`, retain their pre-R7 schemas and corresponding pinned entries. A v4 export may include the established `--study` inputs. Existing Shadow and research routes keep their original arguments and authentication.

## Operator verification and freshness

Install the wheel with `[api,learning]` for the offline publication workflow. The existing `dsv-research-lab-export` accepts an explicit, independently pinned private request alongside its original `--study` options.

```sh
dsv-research-lab-export --visibility-request "$DSV_R7_REQUEST" \
  --visibility-request-pin-file "$DSV_R7_REQUEST_PIN_FILE" --output "$DSV_R7_PUBLICATION"
dsv-api --research-lab-root "$DSV_R7_PUBLICATION" \
  --research-lab-index-sha256 "$DSV_R7_APPROVED_INDEX_SHA256" \
  --research-lab-read-token-env DSV_R7_READ_TOKEN --host 127.0.0.1 --port 8000
```

The private request uses `research_lab_visibility_request_v1` and references individual retained R1–R6 evidence, R5 readback and the KRX receipt. Approve the exported index digest separately before startup. The export verifies the retained owner records and checks original identities, row denominators, source hashes and custody before constructing the typed snapshot. Missing or unverified required sources fail the export. Keep the last approved snapshot with its original timestamps if a refresh fails. Create a new approved snapshot and restart the isolated reader to pick up newly retained evidence. The API never refreshes sources during GET requests. The original stores, local paths, source PINs and checkpoints stay private.

Private request fields below are supplied by the operator. Every file-valued field is an absolute path within the operator's approved source scope. A `*_pin_file` references a separately retained digest file. The legacy readiness list contains `report` and `pin_file` per entry.

| Request field | Nested keys |
| --- | --- |
| `version` | `research_lab_visibility_request_v1` |
| `r1` | `report`, `pin_file` |
| `r2`, `r3`, `r4` | `report`, `plan_pin_file` |
| `r5` | `request`, `request_pin_file`, `journal`, `head_pin_file`, `audit`, `audit_pin_file`, `readback`, `readback_pin_file`, `inventory`, `inventory_pin_file`, `legacy_readiness` |
| `r6_pilot`, `r6_approved` | `report`, `plan_pin_file`, `manifest_sha256` |
| `operations` | `receipt`, `receipt_pin_file`, `scheduler`, `scheduler_pin`, `recovery_scheduler`, `recovery_scheduler_pin_file`, `recovery_plan`, `recovery_plan_pin_file`, `capture_attempt`, `capture_pin_file`, `batch_pin_file`, `launchagent` |

## Verified local evidence

The integration examples below use the 2026-11-28 KST R7 publication. Fields omitted from the examples are defined in Swagger.

| Authenticated request | Selected response fields |
| --- | --- |
| `/overview` | `fits: 58`, `prospectivePredictions: 0`, `prospectiveEvaluations: 0`, `qualifiedAnchors: 0`, `performanceApproved: false` |
| `/prediction-rows?stage=R1&state=evaluated&limit=3` | `total: 2657`, `offset: 0`, `nextOffset: 3`. The correctness filters yield 1,159 correct and 1,498 incorrect. |
| `/experiments?stage=R3` | `total: 34`; R3 kept 31 fits and three exact reuses. |
| `/runs?stage=R6` | `total: 4`. The approved run has `fits: 12`, `evaluations: 12`, `retainedLegacy: 12`, and wall time 190.25 seconds. Three pilot windows account for four fits, four evaluations and one checkpoint reuse. |
| `/prospective` | `status: "BLOCKED"`, `observations: 33`, `requiredObservations: 35`, `legacyPending: 8`, `historicalReplay: 10`. Three original registered models each have zero new predictions. |
| `/krx-status` | `latestTradingDate: "2026-11-20"`, `sessions: 33`, `totalRows: 30981`, `capturedRows: 935`, `duplicateRows: 0`, `policyValid: false`. |

R1’s original development-fold individual scores are unavailable. The R2 source has 914 distinct H5 evaluation rows over two cohorts with 3,656 model–row evaluations after model/variant expansion. R4 has six unique fitted configurations and four retained reference records. The paired H1 fixed-cohort comparisons show 22 and 4 more correct predictions for Legacy than the retained challenger. R6 preserves 12 of 12 completed approved fits and 9 changes among 10 eligible follow-up choices. The stable-improvement gate accepted zero R6 candidates. The web and iOS integration can now use these paths and remains a separate verification step.


The H1 Weekly C=1 pilot model and C=10 approved model each made 367 correct and 313 incorrect predictions on the same 680 rows. Accuracy 0.539706, BA 0.540488 and AP 0.560984 were equal at the available precision. Brier was 0.248677114 for C=1 and 0.248676487 for C=10. All 680 stored probabilities differed, with a maximum absolute difference of 0.000084498. Both original model IDs remain visible. Against the matched Legacy reference for H1 C=10, 171 errors became correct, 131 correct predictions became errors, and FPR rose from 48.10% to 54.81%. Legacy remained the research decision.

For `GET /api/v1/research/lab/prediction-rows?stage=R1&ticker=001060&state=evaluated&limit=1`, the first retained row contains the following fields.

```json
{
  "ticker": "001060",
  "horizon": 5,
  "inputDate": "2026-10-06",
  "baseDate": "2026-10-07",
  "targetDate": "2026-10-15",
  "baseClose": 27650,
  "targetClose": 24400,
  "actualReturn": -0.11754068716094035,
  "probability": 0.4802674393024417,
  "predictedClass": 0,
  "actualClass": 0,
  "correct": true,
  "state": "evaluated",
  "generatedAt": null
}
```

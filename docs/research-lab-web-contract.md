# Donghak AI Research Lab — approved multi-study read API

This is the **complete backend publication contract** for TrisMarketLens. It reuses
the existing immutable autonomous study/experiment artifacts and
`multi_stock_research_report_v1` reports. It does **not** create new training
runs, select or promote an operational model, open Final Test, start Ollama,
request KRX data, or activate unattended scheduling.

## Publication (explicit operator-only, never automatic)

The existing PR #246 one-study read API remains available and unchanged.

After verifying **six** finalized study roots and their original Plan IDs in
the private Mac environment, use the offline exporter. This is an example with
placeholder identifiers, **not** an executable pre-filled private path:

```sh
dsv-research-lab-export \
  --study "/absolute/private/study-A-adaptive:<actual-plan-id>" \
  --study "/absolute/private/study-A-predeclared:<actual-plan-id>" \
  --study "/absolute/private/study-A-robust:<actual-plan-id>" \
  --study "/absolute/private/study-B-adaptive:<actual-plan-id>" \
  --study "/absolute/private/study-B-predeclared:<actual-plan-id>" \
  --study "/absolute/private/study-B-robust:<actual-plan-id>" \
  --output "/absolute/private/new-approved-publication"
```

The exporter validates the original plan via the existing read-only
`context(..., code_check=False)`, creates six consistent SQLite backups, copies
the original six final reports unchanged into a separate private 0700 directory,
writes an index with exact file SHA-256s, and validates all study-report links
before returning the index SHA. **It does not serve the archive.**

Keep the returned index SHA-256 **outside that output directory** for independent
approval. A generated digest is not itself independent authorization. After
operator review, start the local API explicitly:

```sh
dsv-api \
  --research-lab-root "/absolute/private/new-approved-publication" \
  --research-lab-index-sha256 "<independently-approved-index-sha256>" \
  --host 127.0.0.1 --port 8000
```

The server is loopback-only for this source. Its GET handlers have no root path
or filename input; it opens all approved sources once at startup and only
responds with in-memory allowlisted projections. Do not publish the API through
a reverse proxy or expose private research files through a web server. The
web client must connect to its local backend URL.

## Endpoints (all GET)

| Endpoint | Safe data |
| --- | --- |
| `/api/v1/research/lab/studies` | Paginated studies with original Study IDs and report refs |
| `/api/v1/research/lab/studies/{studyId}` | Study scope, policy, selected horizons and approval status |
| `/api/v1/research/lab/studies/{studyId}/experiments` | Paginated 12 actual completed experiment records per finalized study |
| `/api/v1/research/lab/studies/{studyId}/experiments/{experimentId}` | Real Experiment ID, validated parent, model family, AP, fold BA/Brier, baselines, selected/deferred diagnostics |
| `/api/v1/research/lab/studies/{studyId}/memory` | Original Experiment ID lineage, discovery order, cache reuse and memory count |
| `/api/v1/research/lab/reports` | Paginated report catalog |
| `/api/v1/research/lab/reports/{reportId}` | Operator-pinned report projection, research period, completed counts, selected metrics, resources, descriptive limitations |
| `/api/v1/research/lab/comparisons` | Matched condition/cohort policy arms; descriptive performance, never inferred superiority |

All endpoints return a `schemaVersion` field. Study ID is the original
content-addressed plan ID and `experimentId` is its original configuration
identity, not an array index. Report ID is the SHA-256 of the approved report
bytes. Cross-study relationship is **only** established by the original
comparison conditions and cohort; a report ID is never guessed from a filename.

The API exposes the three-arm comparison only if all three policy arms exist in
the exact matching condition group. Each horizon separates the *selected model*
from the *best seen AP*. Any 2026-Q3 diagnostic is marked descriptive and never
used as selection evidence. Different cohorts are kept in separate comparison
groups, even if their policy names match.

## Trust boundary and limitations

- `independentValidationStatus = NOT_ESTABLISHED`, `pitQualified = false`,
  `performanceApproved = false`, `executionAuthorized = false`.
- `policySuperiorityEstablished = false`: descriptive policy differences do
  not prove causal LLM or trading superiority. No model is auto-promoted.
- Raw JSON report documents, report `historical` source references, private
  filesystem paths, dataset/sample rows, KRX originals, checkpoint files,
  Source/Dataset/Plan PIN contents, credentials and training commands are not
  serialized.
- A bad index hash, malformed report, mismatched experiment owner, wrong
  comparison conditions, altered fold metrics or incorrect report status
  **fails startup closed**.
- Real-data E2E requires the separately authorized operator-owned snapshots.
  Synthetic fixture success does not establish the actual 72/6 collection
  identity until the approved local publication is validated.

## Web integration

The client should fetch the studies catalog, then each study's experiments,
memory, and report by returned IDs. Use `comparisons` for cross-policy cards.
Do **not** fabricate a missing ID or present deferred metrics as independent
future performance. Keep existing unsupported views labeled unavailable until
their actual backing evidence is published.

The original `/api/v1/research/studies` PR #246 endpoints stay backward
compatible and require their own explicit single-study snapshot configuration.

# Provider-independent research model management

This optional backend control plane selects the **research hypothesis LLM**, not
the trained stock-direction model. It reuses ResearchAgent, the existing finite
experiment engine, original snapshots, feature/label contracts, AnalysisStore,
checkpoint verification and the common proposal validator. No operating model,
market collector, scheduler, old study/PIN or investment permission is changed.
TrisMarketLens, TrisMarketLens-iOS and the NEWS repository are not modified.
NEWS PR #155 informed the provider/settings separation only; its execution and
source-transfer policies are not imported into market research.

## Architecture and ownership

```
Authenticated REST / managed research CLI
  -> ModelManagement (immutable catalog, validation, selection, audit, lease)
  -> ResearchAgent / LLMResearchAgent (bounded summary + common validator)
  -> LLMProvider / ProviderFactory
       -> OllamaProvider (existing local installation, CPU and verified release)
       -> OpenAICompatibleProvider (approved HTTPS config/credential reference)
  -> existing Python experiment worker / evaluation / memory
```

The engine accepts an audited agent factory; new provider transport does not
require editing its learning algorithm. The original `--agent gemma4` facade and
rule-based default remain supported. The generic opt-in is `--agent managed`.
Gemma remains the example/default registered LLM; registering a model never runs
it. A changed LLM cannot promote/register a stock model, open Final Test or widen
the frozen candidate space. JSON retains the historical
`gemma_research_proposal_v1` schema identifier for compatibility across providers.

New v3 studies have a managed-agent policy instead of a pinned Gemma model.
Each run leases a validated immutable ModelConfig and records its provider/model,
revision and configuration fingerprint independently of original source pins.
The bounded `management_masks_v1` space contains twelve new causal mask/model
combinations, disjoint from the older spaces. A single run keeps its chosen
configuration even if a later selection is requested. The selection lock and
common heavy-work reservation reject concurrent changes with HTTP 409.
The existing v1/v2 study identifiers and reports are not rewritten.

## Server configuration and credentials

ModelConfig includes provider/model identity, display version/revision, approved
endpoint, timeout, temperature, context/output limits, explicit capability claims,
CPU/remote execution policy, estimated/max memory and CPU watchdog. Types/ranges
are validated. Unknown capabilities stay unknown; unsupported requests fail.
Ollama installation/digest and advertised thinking support are checked against
actual metadata. Successful bounded inference establishes compatibility with the
research output contract, not a universal guarantee of every JSON schema feature.

The server configuration is a private mode-0600 JSON file with a separately
supplied SHA-256. HTTP clients can submit a registered **model key only**, never an
endpoint, credential reference, configuration, resource override or source path.
Catalog additions are append-only: old definitions/default stay immutable; use a
new model key for a revised configuration, explicitly validate and select it.

```json
{
  "version": "ai_management_server_v1",
  "default": "gemma-default",
  "models": [{
    "key": "gemma-default", "provider_id": "ollama",
    "model_id": "gemma4:e2b", "display_name": "Gemma 4 E2B",
    "revision": "operator-retained-installed-model-digest",
    "endpoint": "http://127.0.0.1:11434",
    "structured_output": true, "json_schema": true,
    "thinking_supported": true, "thinking": false,
    "estimated_memory_mb": 6144, "max_memory_mb": 7168
  }],
  "auth": [{
    "subject": "approved-operator", "secret_env": "EXISTING_AI_MANAGEMENT_TOKEN",
    "scopes": ["ai:read", "ai:manage"]
  }]
}
```

Replace the digest placeholder with independently retained installed identity;
never download/relabel a model to force compatibility. Credentials are injected
from existing private environment or a trusted principal resolver. The existing
snapshot API has no account-authentication owner, so model controls add an explicit
opt-in scoped bearer adapter, rather than trusting localhost. Production gateways
can inject their verified identity resolver into `create_app`. Raw tokens are
hashed in the adapter and are not persisted or returned. Missing auth configuration
fails startup; unconfigured management routes are absent from the ordinary API.
Old snapshot routes retain their contracts and read-only behavior.

A remote entry uses `provider_id: openai_compatible`, `execution_policy: remote`,
an operator-approved HTTPS base ending in e.g. `/v1`, a `credential_env` reference,
and a model/revision. Execution additionally requires `remote_enabled: true` and
`approved_transfer_scope: bounded_research_summary_v1`. Neither is enabled by
HTTP requests. Only the existing bounded whitelist of research summaries crosses
that boundary: no raw KRX responses/rows, source PINs, paths, credentials, model
weights or confirmation labels. Proxies and redirects are disabled. Unsupported
thinking mappings, tool calls, incomplete output, model mismatches and echoed
credentials are rejected. No actual paid/remote provider call is necessary for
installation; configured, reachable and validated are distinct states. Remote
revision is a configured/reported identity, not independent cryptographic authority.

## REST contract for both clients

All routes below require `Authorization: Bearer ...`; `ai:read` permits queries and
`ai:manage` additionally permits validation/selection. Neither client calls Ollama
or a remote LLM directly. OpenAPI declares HTTPBearer and input/registry/active/
validation schemas. Unknown body fields are forbidden and bodies are capped at
4096 bytes. CORS permits configured web origins only; no cookie-based authority is
inferred. The server returns standard FastAPI `detail` errors.

| Method/path under `/api/v1/ai/models` | Behavior |
| --- | --- |
| `GET /` (without trailing slash) | Registered models plus unregistered installed Ollama entries; no loading |
| `GET /active` | Default, selected config/version, actual last applied research model, busy state |
| `POST /validate` | 202 bounded validation job; no research run or selection change |
| `GET /validations/{jobId}` | Job state and completed validation receipt |
| `POST /validate?wait=true` | Synchronous operator alternative; may exceed mobile timeout |
| `PUT /active` | Approved compare-and-set selection, effective next managed research run |
| `GET /history` | Persisted successful/rejected change audit and actor |
| `GET /comparisons` | Model/run summaries, comparable only for identical comparisonContractId |

Validation is asynchronous by default because the existing iOS ResearchAPIClient
uses a ten-second transport timeout. At most one job executes; interrupted queued
jobs are not automatically replayed. An explicit new validation request can retire
an unfinished job after restart. Selection while validation/research holds the
lease is rejected; no currently running model is swapped underneath a worker.
Validation receipts bind the exact model config and expire after one hour.

Example requests (tokens and receipt IDs are placeholders):

```http
POST /api/v1/ai/models/validate
Authorization: Bearer <management-token>
Content-Type: application/json

{"modelKey":"gemma-default"}
```

```json
{"jobId":"<64-hex-id>","state":"queued","statusUrl":"/api/v1/ai/models/validations/<64-hex-id>","researchStarted":false,"modelChanged":false}
```

Poll `statusUrl`; a completed result contains `validationId`, `modelKey`, `valid`,
`reason`, `expiresAt`, `unloaded`, `researchStarted:false`, `modelChanged:false`.
Then read `/active` and use its current selectionVersion:

```http
PUT /api/v1/ai/models/active
Authorization: Bearer <management-token>
Content-Type: application/json

{"modelKey":"gemma-default","validationId":"<receipt-id>","expectedSelectionVersion":0,"approve":true}
```

`/active` distinguishes `defaultModelKey`, `selectedModelKey`, `selectedModel`,
`selectionVersion`, `effectiveFrom:next_managed_research_run`, and nullable
`lastApplied`. A successful validation is not an applied research model. Selection
history persists separately from actual proposal-use events. Registry rows expose
provider/model/revision, installed/configured/reachable/available/executable,
capabilities, resource requirements, validation time, selected, canChange and
changeBlockers. Unknown installed models are unavailable until server registration;
unknown remote reachability is not reported as successful access.

Clients should handle 401 (authenticate), 403 (permission), 409 (busy/stale version),
413 (body bound) and 422 (unknown/unvalidated/unsupported/unapproved conditions).
Do not blind-retry expensive validation jobs. Swift and web code can consume the
same JSON/OpenAPI contract; UI/token-gateway wiring remains a separate client task.

## CLI and deployment preparation

```sh
dsv-api --ai-management-only --ai-management-root "$CONTROL" --ai-model-config "$CONFIG" --ai-model-config-sha256 "$CONFIG_SHA" --host 127.0.0.1
# A new separate study, retaining the original source/snapshot PINs:
dsv autonomous-research derive --source-root "$PARENT" --source-plan-pin-file "$PARENT_PINS/plan.pin" --model-digest-file "$ANCHOR_MODEL_PIN" --managed-agents --root "$STUDY" --pins-root "$PINS"
dsv autonomous-research run --root "$STUDY" --plan-pin-file "$PINS/plan.pin" --agent managed --management-root "$CONTROL" --model-config "$CONFIG" --model-config-sha256 "$CONFIG_SHA" --steps 1
```

The anchor digest is retained only for compatibility of derivation from the old
study; the v3 plan has no fixed Gemma execution model. All roots share the same
Research parent so API controls, provider validation and learner runs use one
heavy-work reservation. The controller root must be new/owned, not an old study or
market DB directory. `--operational-only` cannot enable management. Nothing here
installs/activates a scheduler, replaces running services or changes source policy.

## Resource and comparison policy

Ollama reuses original load/release/native-process protections, CPU-only options,
one learner, timeouts, memory/disk/AC/thermal guards and model identity checks.
The estimate cannot be below the installed artifact size; it must fit actual host
free memory plus reserve before loading. Oversized models are rejected, not loaded
because a name appears in the registry. A failed/native-unconfirmed release blocks
learning; unrelated resident models are not killed. CPU thread options are not a
kernel-wide CPU quota. No MLX or new model download is introduced.

Comparison records retain LLM config/revision, selected experiments/hypotheses,
valid/rejected proposals, fresh fits/cache hits, internal metrics, timing/resources
and failure state. The comparison fingerprint binds source/dataset, candidate
space, budget, folds, implementation and both learner/LLM safety policies. Different
conditions must not be pooled as a controlled comparison. Existing exact-plan
checkpoint/result reuse avoids unnecessary duplicate fits. Development scores and
contract probes are never Final Test or independent future-performance evidence.
All PIT/performance/execution approvals and operational registration remain false.

## Local execution evidence and limits

A separate private control directory and managed research studies were used on the
existing host; no operating configuration was activated. Both already-installed
`gemma4:e2b` and `gemma2:latest` passed actual CPU-only output-contract probes.
Authenticated REST selection switched between them, survived controller restart,
and rejected a change during a held run lease. A real loopback HTTP validation
returned 202 in approximately 0.007 seconds; polling observed successful completion
without changing the selected model. No model was downloaded.

The full-history Gemma 2 proposal exceeded its 600-second ceiling; verified release
preceded one safe Rule-Based fit. An initial full-history Gemma 4 proposal invented
a parent experiment identifier and was rejected by the existing ownership check,
also followed by a safe fallback fit. The schema now restricts parent identifiers
to verified history (or null), matching that unchanged validator. Neither failed
proposal is credited as an LLM-selected experiment. Contract-probe success alone
therefore does not establish full-context reliability or comparative model quality.

Remote adapter behavior is tested with injected HTTP transports, including denied
transfer, wrong identities and credential echoes. No live remote provider, paid
service or KRX request was used. Client UI/authentication gateway integration and
production service activation remain separate work; selection affects only an
explicitly managed research run, never the existing operating scheduler.

After that schema correction, a new immutable managed study successfully used the
actual Gemma 4 model: twelve prior experiment records were delivered, one proposal
passed the common validator, verified model release preceded one real Python fit,
and the applied-model receipt and comparison metrics were persisted. Source PIN
verification succeeded; KRX/remote requests, confirmation-gate consumption and
approval promotions were all zero. Earlier failed studies remain retained; their
PINs and observations were not rewritten. This validates the local integration,
not comparative LLM quality or stock-direction performance.

# Upstream Gap Integration Design

Date: 2026-04-09
Branch: `spark-main`
Related triage: [2026-04-09-upstream-gap-triage.md](/Users/wzh/IsacHermes/docs/plans/2026-04-09-upstream-gap-triage.md)
Upstream snapshot: `upstream-temp/main @ 989d4ea4`

## Goal

This document converts the remaining upstream feature gap into an executable
software design. It intentionally goes beyond triage and records:

- target files and functions
- non-goals and conflict boundaries with existing local architecture
- edge cases and exception paths
- exact test additions / updates required before merge

This design only covers **features not yet merged** from the current upstream
delta. Already integrated items are excluded except where they constrain new
work.

## Hard Implementation Rules

1. Preserve all local ContextEngine, request-metrics, and SparkGraph behavior.
2. Do not overwrite local architecture with upstream module rewrites when the
   subsystem has materially diverged.
3. For every feature:
   - add at least one regression test reproducing the upstream bug or feature
     expectation
   - add/update integration coverage in the touched subsystem
4. For changes touching message conversion, Anthropic transport, or request
   shaping, final metrics must still reflect the transport-visible payload.
5. No speculative rewrites: land the minimal architecture-native fix that
   preserves the local design center.

## Required Architecture Additions

The following additions are mandatory to avoid patch-style implementation.

### 1. `StreamTimeoutPolicy`

Used by Feature B.

Purpose:

- centralize stale-stream timeout decisions
- prevent local-provider checks from being scattered through the streaming loop

Required shape:

- a small resolver/helper layer returning the effective stale timeout policy
- the streaming loop must consume policy output instead of recomputing conditions

### 2. `AnthropicEndpointPolicy`

Used by Feature D.

Purpose:

- centralize endpoint classification for official Anthropic vs. third-party
  Anthropic-compatible endpoints
- keep conversion, kwargs construction, and metric-view generation aligned

Required shape:

- a helper or lightweight dataclass carrying endpoint compatibility facts
- conversion functions must accept policy, not raw ad-hoc booleans

### 3. Provider Integration Contract

Used by Features F and G.

Purpose:

- avoid `if provider == ...` logic spreading across auth, runtime resolution,
  status, and model switching

Required shape:

- provider registration at the existing provider/runtime abstraction boundary
- auth resolution and runtime provider selection must remain registry-driven

### 4. Execution Compatibility Layer

Used by Feature H.

Purpose:

- prevent the spawn-per-call migration from becoming a cross-file rewrite

Required shape:

- an explicit execution adapter boundary
- dual-path migration support during rollout

---

## Feature A: OpenRouter Variant Tag Preservation

### Upstream commits

- `980fadfe`

### Product behavior

Preserve model ids containing OpenRouter variant suffixes:

- `vendor/model:free`
- `vendor/model:fast`
- `vendor/model:extended`

while still supporting legacy shorthand input:

- `vendor:model`
- `vendor:model:free`

### Local conflict analysis

Upstream fixed this inside a newer catalog-aware `model_switch.py` pipeline.
Our local implementation is simpler and splits responsibility across:

- [`/Users/wzh/IsacHermes/hermes_cli/models.py`](/Users/wzh/IsacHermes/hermes_cli/models.py)
  - `parse_model_input()`
  - `detect_provider_for_model()`
- [`/Users/wzh/IsacHermes/hermes_cli/model_switch.py`](/Users/wzh/IsacHermes/hermes_cli/model_switch.py)
  - `switch_model()`

We must not cherry-pick the upstream `model_switch.py` flow wholesale.

### Design

#### Step A1: Lock expected behavior with tests

Add:

- [`/Users/wzh/IsacHermes/tests/hermes_cli/test_model_switch_variant_tags.py`](/Users/wzh/IsacHermes/tests/hermes_cli/test_model_switch_variant_tags.py)

Required cases:

1. `openrouter` current provider, input already `vendor/model:free`
   - result must remain unchanged
2. `openrouter` current provider, input `vendor:model`
   - result should normalize to `vendor/model`
3. `openrouter` current provider, input `vendor:model:free`
   - only first colon becomes `/`
4. bare model name resolution still behaves unchanged
5. non-aggregator providers still use existing parsing behavior

#### Step A2: Constrain parsing, not the full switch pipeline

Target functions:

- [`parse_model_input()`](/Users/wzh/IsacHermes/hermes_cli/models.py#L372)
- [`switch_model()`](/Users/wzh/IsacHermes/hermes_cli/model_switch.py#L39)

Implementation rule:

- if the input already contains `/`, a later `:` must be treated as a variant
  suffix and not as a provider/model separator
- if the input does not contain `/` and is on an aggregator path, legacy
  `vendor:model[:variant]` shorthand may still be normalized

We should prefer patching the smallest place where conversion currently happens
or is inferred. Do not introduce alias/catalog logic from upstream that does not
exist locally.

### Edge cases

1. `custom:local:qwen` triple-colon custom syntax must remain intact
2. bare provider names like `/model anthropic` must still work
3. already-normalized OpenRouter ids without variants must remain unchanged
4. non-OpenRouter providers must not inherit aggregator-only normalization

### Exceptions / failure handling

No new error path should be introduced. When parsing fails, current fallbacks
must still return existing user-facing validation errors.

### Test matrix

- new: `tests/hermes_cli/test_model_switch_variant_tags.py`
- existing:
  - [`/Users/wzh/IsacHermes/tests/hermes_cli/test_models.py`](/Users/wzh/IsacHermes/tests/hermes_cli/test_models.py)
  - any existing model switch tests

---

## Feature B: Local Provider Stale Stream Timeout Fix

### Upstream commits

- `ae4a884e`

### Product behavior

When the agent talks to a local model endpoint, stale-stream timeout must not
abort a request just because the provider is still performing a long prefill.

Explicit user-configured stale timeout must still win.

### Local conflict analysis

This change lands in the core loop:

- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)

Our local file already contains heavy customizations:

- ContextEngine request assembly
- request metrics
- tool-result persistence
- Anthropic metric-view handling
- retry/backoff work

So we must inject the fix at the timeout-decision boundary, not rewrite the
streaming loop.

### Design

#### Step B1: Add `StreamTimeoutPolicy`

Before changing any stream timeout behavior, introduce a dedicated policy layer.

Target location:

- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)
  or a small adjacent runtime helper module if extraction is cleaner

Required API:

- `_resolve_stream_timeout_policy(...) -> StreamTimeoutPolicy`

Minimum policy fields:

- `stale_timeout_seconds`
- `is_user_explicit`
- `disabled_for_local`

The streaming loop must only consume policy output.

#### Step B2: Locate stale timeout derivation

Target current stale-stream area around:

- [`run_agent.py` around stale stream detection](/Users/wzh/IsacHermes/run_agent.py#L5130)

Inputs to the resolver:

- configured stale timeout
- whether user explicitly set `HERMES_STREAM_STALE_TIMEOUT`
- effective base URL
- provider/runtime identity if already resolved

Policy behavior:

1. if user explicitly configured stale timeout -> honor it
2. else if endpoint is local -> disable stale timeout
3. else -> keep existing default behavior

#### Step B3: Reuse robust local-endpoint detection

Do not hand-roll new substring matching. Either:

- reuse an existing local-endpoint helper if present, or
- port upstream’s logic into a dedicated local helper near runtime/provider
  detection code

That helper must correctly classify:

- `localhost`
- `127.0.0.1`
- RFC1918 private ranges
- WSL host mappings if we already support them

### Edge cases

1. local endpoint via `httpx.URL`
2. local endpoint with explicit stale timeout in env/config
3. cloud endpoint with no explicit timeout
4. custom endpoint pointing to LAN IP
5. OpenRouter / remote providers must remain unchanged

### Exceptions / failure handling

If local-endpoint detection itself fails, fail open to current behavior rather
than disabling timeout silently for all providers.

### Test matrix

Add:

- [`/Users/wzh/IsacHermes/tests/test_run_agent_local_stream_timeout.py`](/Users/wzh/IsacHermes/tests/test_run_agent_local_stream_timeout.py)

Cases:

1. local endpoint + no explicit timeout -> stale timeout disabled
2. local endpoint + explicit timeout -> explicit timeout preserved
3. remote endpoint -> unchanged
4. `httpx.URL` base_url works

Broader regression:

- [`/Users/wzh/IsacHermes/tests/test_run_agent.py`](/Users/wzh/IsacHermes/tests/test_run_agent.py)

---

## Feature C: `flush_memories` Config Timeout

### Upstream commits

- `42e366f2`

### Product behavior

`flush_memories()` must use configured timeout values instead of hardcoded `30s`
in both:

- auxiliary LLM path
- direct OpenAI fallback path

### Local conflict analysis

Touched code:

- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)
  - [`flush_memories()`](/Users/wzh/IsacHermes/run_agent.py#L5837)

This is a correctness fix and should integrate cleanly if we stay focused on
timeout plumbing only.

### Design

#### Step C1: Remove hardcoded timeout from auxiliary path

Current hardcoded timeout usage must be replaced with our canonical timeout
resolution path:

- `_get_task_timeout("flush_memories")`
or the nearest existing helper already used by auxiliary tasks.

#### Step C2: Align direct fallback path

The direct OpenAI fallback call currently uses an explicit `timeout=30.0`.
Replace it with the same resolved timeout used by the auxiliary path.

### Edge cases

1. timeout configured as integer
2. timeout configured as float
3. timeout missing -> default unchanged
4. auxiliary disabled -> direct path still honors config

### Exceptions / failure handling

Timeout resolution failure must fall back to current default behavior rather
than crashing memory flush.

### Test matrix

Extend:

- [`/Users/wzh/IsacHermes/tests/test_flush_memories_codex.py`](/Users/wzh/IsacHermes/tests/test_flush_memories_codex.py)

Required cases:

1. auxiliary path uses configured timeout
2. direct fallback uses configured timeout
3. no config -> default retained

---

## Feature D: Third-Party Anthropic Endpoint Signature Handling

### Upstream commits

- `875a72e4`

### Product behavior

When targeting third-party Anthropic-compatible endpoints:

1. accept `base_url` values that may be `httpx.URL`
2. strip Anthropic-only thinking signatures those providers cannot validate

Official Anthropic endpoint behavior must remain unchanged.

### Local conflict analysis

This overlaps with local work already integrated in:

- [`/Users/wzh/IsacHermes/agent/anthropic_adapter.py`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py)
- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)

We also have a hard requirement that final request metrics match the
transport-visible payload. This makes a raw cherry-pick unsafe.

### Design

#### Step D1: Add `AnthropicEndpointPolicy`

Before changing conversion behavior, introduce a policy layer to prevent ad-hoc
endpoint checks from leaking across multiple functions.

Target location:

- [`/Users/wzh/IsacHermes/agent/anthropic_adapter.py`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py)

Required API:

- `_resolve_anthropic_endpoint_policy(base_url, provider_name=None, api_mode=None)`

Minimum policy fields:

- `base_url_str`
- `is_official_anthropic`
- `is_third_party_compatible`
- `strip_thinking_signatures`

Both request conversion and metric-view conversion must consume this policy.

#### Step D2: Normalize `base_url` before endpoint classification

Target helper families in:

- [`/Users/wzh/IsacHermes/agent/anthropic_adapter.py`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py)

Any helper that currently assumes `.rstrip()` on a string must first coerce:

- `base_url = str(base_url or "")`

#### Step D3: Make signature stripping endpoint-aware

Target functions:

- [`convert_messages_to_anthropic()`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py#L1021)
- [`convert_messages_to_anthropic_metric_view()`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py#L1261)

Design requirement:

- both conversion paths must accept the endpoint context needed to decide
  whether to preserve or strip signatures

Expected behavior:

1. official Anthropic endpoint
   - keep current signed-thinking behavior
2. third-party Anthropic-compatible endpoint
   - strip or downgrade incompatible signatures before payload construction
3. metric-view conversion
   - must mirror the transport-visible decision so final metrics do not drift

#### Step D4: Thread endpoint identity through runtime call sites

Target:

- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)

Any call site building Anthropic kwargs or metric views must pass the effective
base URL / endpoint classification consistently.

### Edge cases

1. `base_url` is `httpx.URL`
2. Anthropic official endpoint with signed thinking
3. third-party endpoint with signed thinking
4. third-party endpoint with redacted thinking blocks
5. prompt caching + third-party endpoint
6. metric-view path after sanitizer and adapter transformations

### Exceptions / failure handling

If endpoint classification fails, default to the safer option for compatibility:

- preserve official behavior for clearly official endpoints
- otherwise strip signatures rather than sending invalid ones to proxies

### Test matrix

Add:

- [`/Users/wzh/IsacHermes/tests/test_anthropic_adapter_third_party_signatures.py`](/Users/wzh/IsacHermes/tests/test_anthropic_adapter_third_party_signatures.py)

Extend:

- [`/Users/wzh/IsacHermes/tests/test_anthropic_adapter.py`](/Users/wzh/IsacHermes/tests/test_anthropic_adapter.py)
- [`/Users/wzh/IsacHermes/tests/context_engine/test_run_agent_request_metrics.py`](/Users/wzh/IsacHermes/tests/context_engine/test_run_agent_request_metrics.py)

Required cases:

1. `httpx.URL` base_url no longer crashes
2. official Anthropic endpoint keeps signed thinking blocks
3. third-party endpoint strips incompatible signatures
4. metric-view matches final transport-visible payload

---

## Feature E: Gateway Staged Inactivity Warning

### Upstream commits

- `09206171`

### Product behavior

Before a gateway session reaches hard inactivity timeout, the user should
receive a single warning notification and get a chance to intervene.

### Local conflict analysis

Touched code:

- [`/Users/wzh/IsacHermes/gateway/run.py`](/Users/wzh/IsacHermes/gateway/run.py)
- [`/Users/wzh/IsacHermes/hermes_cli/config.py`](/Users/wzh/IsacHermes/hermes_cli/config.py)
- [`/Users/wzh/IsacHermes/cli-config.yaml.example`](/Users/wzh/IsacHermes/cli-config.yaml.example)

This is mostly local to gateway lifecycle behavior and should integrate well
if we preserve the current timeout model.

### Design

#### Step E1: Add warning threshold config

Config fields:

- `agent.gateway_timeout_warning`
- env override aligned with upstream naming

Rules:

- `0` disables the warning layer
- warning threshold must be lower than hard timeout to be active

#### Step E2: Track warning emission state per session

Target:

- gateway inactivity monitoring loop in [`/Users/wzh/IsacHermes/gateway/run.py`](/Users/wzh/IsacHermes/gateway/run.py)

Implementation:

- add session-local warning state
- emit warning once when threshold is crossed
- clear warning state when activity resumes

#### Step E3: Preserve current hard-timeout semantics

The final inactivity timeout flow must remain unchanged except for the added
pre-timeout warning.

### Edge cases

1. warning threshold disabled
2. warning threshold >= hard timeout
3. user activity after warning but before timeout
4. multiple gateway sessions in parallel
5. platforms with different notification formatting

### Exceptions / failure handling

If warning dispatch fails, do not break the hard-timeout path. Log and continue.

### Test matrix

Add:

- [`/Users/wzh/IsacHermes/tests/gateway/test_gateway_inactivity_timeout.py`](/Users/wzh/IsacHermes/tests/gateway/test_gateway_inactivity_timeout.py)

Required cases:

1. warning emitted once before timeout
2. warning reset after activity
3. timeout still fires when inactivity continues
4. disabled warning emits nothing

---

## Feature F: Qwen OAuth Provider

### Upstream commits

- `3377017e`
- `5d2fc6d9`

### Product behavior

Add Qwen OAuth-based provider support with portal request handling and supporting
auth/status/runtime flows.

### Local conflict analysis

This is a provider feature, not a patch. It touches:

- [`/Users/wzh/IsacHermes/hermes_cli/auth.py`](/Users/wzh/IsacHermes/hermes_cli/auth.py)
- [`/Users/wzh/IsacHermes/hermes_cli/auth_commands.py`](/Users/wzh/IsacHermes/hermes_cli/auth_commands.py)
- [`/Users/wzh/IsacHermes/hermes_cli/models.py`](/Users/wzh/IsacHermes/hermes_cli/models.py)
- [`/Users/wzh/IsacHermes/hermes_cli/runtime_provider.py`](/Users/wzh/IsacHermes/hermes_cli/runtime_provider.py)
- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)

This should be integrated as a provider module addition, not as incidental edits
across unrelated code.

### Design

#### Step F1: Use a provider-native integration contract

Do not spread Qwen OAuth handling across unrelated conditionals.

Required integration points:

- provider registry entry
- runtime provider resolution entry
- auth/provider status entry

The implementation must remain registry-driven.

#### Step F2: Add provider definition

Target registries/config:

- auth provider registry
- runtime provider resolution
- model/provider labeling if required

#### Step F3: Integrate auth flow and commands

Target:

- provider status checks
- login/token acquisition flow
- auth command surfaces

#### Step F4: Route runtime selection safely

The provider must integrate into the existing runtime selection chain without
changing current provider precedence for existing users.

### Edge cases

1. Qwen OAuth configured but token expired
2. provider selected without auth
3. provider status in CLI when partially configured
4. `/model` switching to Qwen provider

### Exceptions / failure handling

Auth failures must degrade into current provider resolution errors and must not
silently fall through to unrelated providers.

### Test matrix

Add or extend:

- provider auth tests
- runtime provider tests
- model switch tests
- one `run_agent` smoke test for provider selection

Proposed files:

- `tests/hermes_cli/test_qwen_oauth_provider.py`
- `tests/test_runtime_provider_resolution.py`

---

## Feature G: Voxtral STT Provider

### Upstream commits

- `5f4b93c2`
- `d46db0a1`

### Product behavior

Add Mistral/Voxtral as an STT provider for transcription tools.

### Local conflict analysis

This feature is relatively isolated and should integrate without touching
ContextEngine or core loop logic, aside from provider config wiring.

### Design

#### Step G1: Use existing provider registration boundaries

Voxtral must be added through the current transcription/provider extension
surface rather than one-off branching in tool execution.

#### Step G2: Add provider implementation

Target:

- [`/Users/wzh/IsacHermes/tools/transcription_tools.py`](/Users/wzh/IsacHermes/tools/transcription_tools.py)

Prefer upstream provider implementation and import-path fix as-is unless local
transcription abstractions require thin adaptation.

#### Step G3: Expose config and dependency metadata

Target:

- [`/Users/wzh/IsacHermes/hermes_cli/config.py`](/Users/wzh/IsacHermes/hermes_cli/config.py)
- [`/Users/wzh/IsacHermes/pyproject.toml`](/Users/wzh/IsacHermes/pyproject.toml)

### Edge cases

1. provider selected without SDK installed
2. provider selected without API key
3. fallback to existing provider behavior unchanged

### Test matrix

Add:

- `tests/tools/test_transcription_voxtral.py`

Cover:

1. happy path
2. missing credential
3. missing dependency/import path correctness

---

## Feature H: Unified Spawn-Per-Call Execution Layer

### Upstream commits

- `d684d7ee`
- `e19252af`

### Product behavior

Replace mixed persistent-shell / one-shot execution behavior with a unified
spawn-per-call model that reapplies session state per execution.

### Local conflict analysis

This is a subsystem redesign. Touched areas include:

- [`/Users/wzh/IsacHermes/tools/environments/base.py`](/Users/wzh/IsacHermes/tools/environments/base.py)
- local/docker/ssh/modal/daytona/singularity backends
- [`/Users/wzh/IsacHermes/tools/terminal_tool.py`](/Users/wzh/IsacHermes/tools/terminal_tool.py)
- [`/Users/wzh/IsacHermes/tools/code_execution_tool.py`](/Users/wzh/IsacHermes/tools/code_execution_tool.py)

We must not attempt this as a background cherry-pick. It needs its own migration
track.

### Design

#### Step H1: Define `ExecutionCompatibilityLayer`

Before touching any backend implementation, define a compatibility boundary.

Required abstractions:

- execution request contract
- execution result contract
- cancellation/process handle contract
- environment snapshot contract

These can be dataclasses, protocols, or adapter interfaces, but they must be
named and explicit before backend migration starts.

#### Step H2: Introduce compatibility boundary

Before touching backend implementations, document a compatibility layer between:

- current terminal/tool expectations
- upstream spawn-per-call environment contract

That layer must define:

- command execution contract
- environment snapshot contract
- process handle / cancellation contract
- file sync contract

#### Step H3: Add dual-path migration support

Required mechanism:

- old execution path remains available during rollout
- new spawn-per-call path is gated per backend or via a migration flag
- parity tests must pass before flipping a backend

#### Step H4: Backend-by-backend migration

Do not switch all backends at once.

Suggested order:

1. local
2. docker
3. ssh
4. modal/daytona/singularity

Each backend must be validated before the next moves.

### Edge cases

1. background processes
2. environment mutations across commands
3. shell aliases/functions persistence
4. cancellation semantics
5. file sync correctness

### Exceptions / failure handling

Need dual-path fallback during migration:

- old environment execution path
- new spawn-per-call path

Do not cut over all backends without parity checks.

### Test matrix

This requires a dedicated design and test project. At minimum:

- environment unit tests
- terminal integration tests
- code execution regression tests
- background process tests

---

## Feature I: Hindsight Config Fix

### Upstream commits

- `a1213d06`

### Product behavior

Fix Hindsight memory config key mismatch and add base URL support.

### Design

Only implement if the corresponding Hindsight integration still exists locally.
If so:

- align config keys
- add base URL passthrough
- keep provider disabled unless configured

### Test matrix

- provider-specific unit tests only

---

## Feature J: Nix Packaging Fixes

### Upstream commits

- `8de91ce9`
- `8385f54e`

### Product behavior

Improve Nix interactive CLI and voice dependency support.

### Design

Treat as packaging-layer work:

- do not mix with runtime behavior changes
- validate only if corresponding Nix files still exist locally

### Test matrix

- packaging smoke only

---

## Cross-Feature Combination Tests

These are required because local divergence risk is concentrated in cross-layer
interactions.

### Required combinations

1. Anthropic third-party endpoint + request metrics final-payload alignment
2. local provider timeout behavior + retry/backoff path
3. `flush_memories` timeout + SparkGraph-integrated flush path
4. gateway warning threshold + existing session timeout flow

---

## Implementation Sequence Constraint

The remaining upstream gap should not be implemented as one mixed patch.

Required sequence:

1. Feature A + C
2. Feature B + D
3. Feature E
4. Feature F + G
5. Feature H as a separate project

Each step must pass targeted subsystem tests and full `tests/` before the next.

# Official Feature Integration Roadmap

Date: 2026-04-09  
Status: Proposed  
Scope: Integrate high-value upstream Hermes features into our fork while preserving our ContextEngine / SparkGraph architecture

## 1. Goal

This roadmap defines how to absorb selected upstream Hermes features into our branch in a way that:

1. Preserves our current architectural direction:
   - `ContextEngine`
   - request-level context governance
   - `ToolGroup`
   - tool hot/warm/cold shaping
   - SparkGraph
2. Reuses upstream code whenever it is already correct and scoped
3. Avoids duplicate future work by explicitly recording what has already been absorbed
4. Extends ContextEngine management and observability instead of bypassing it
5. Ships with full test coverage from the start

## 2. Baseline: Already Absorbed

The following upstream capability is already integrated and should be treated as completed baseline, not a future TODO:

### 2.1 Tool Result Persistence

Source inspiration:
- upstream commits `77c5bc9d`, `bbcff8dc`

Integrated locally via:
- [`/Users/wzh/IsacHermes/tools/budget_config.py`](/Users/wzh/IsacHermes/tools/budget_config.py)
- [`/Users/wzh/IsacHermes/tools/tool_result_storage.py`](/Users/wzh/IsacHermes/tools/tool_result_storage.py)
- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)
- [`/Users/wzh/IsacHermes/agent/context_engine/request_metrics.py`](/Users/wzh/IsacHermes/agent/context_engine/request_metrics.py)
- [`/Users/wzh/IsacHermes/agent/context_engine/tool_compaction.py`](/Users/wzh/IsacHermes/agent/context_engine/tool_compaction.py)
- [`/Users/wzh/IsacHermes/cli.py`](/Users/wzh/IsacHermes/cli.py)

Current state:
- large tool outputs can be persisted to sandbox
- persisted previews are tracked in request metrics via `messages_tool_persisted`
- persistence is aligned after sanitizer and reflected in final request metrics
- ContextEngine remains the governing layer over upstream persistence

This roadmap must not reopen or redesign this area unless a concrete regression is discovered.

## 3. Candidate Features

This roadmap covers six upstream features:

1. Session boundary plugin hooks
2. Self-optimized GPT/Codex tool-use guidance
3. MCP structured content preservation
4. `no_mcp` platform sentinel
5. Jittered retry backoff
6. Anthropic thinking block signature management

## 4. Integration Principles

### 4.1 Architectural Principle

Every adopted feature must land in one of these layers:

1. Runtime behavior layer
   - agent execution
   - gateway behavior
   - tool execution
2. ContextEngine governance layer
   - what enters request context
   - how it is categorized and measured
   - how it is exposed to CLI breakdown
3. Plugin / lifecycle layer
   - session lifecycle hooks
   - plugin event boundaries

We should avoid adding logic directly to ad hoc call sites when the feature naturally belongs to one of the layers above.

### 4.2 Reuse Principle

When upstream code is:
- small
- self-contained
- already well tested
- aligned with our architecture

we should import it with minimal adaptation.

When upstream code is:
- coupled to a different architecture
- missing ContextEngine compatibility
- bypassing our request metrics model

we should preserve the behavior but re-home the integration points.

### 4.3 Metrics Principle

Every integrated feature that changes the effective request or tool surface must be reflected in ContextEngine-facing metrics or sidecars.

We must not adopt new runtime behavior that changes model input without deciding:
- how it is measured
- how it is displayed
- whether it needs a new bucket or sidecar dimension

## 5. Feature-by-Feature Design

---

## 5.1 Session Boundary Plugin Hooks

### 5.1.1 Upstream Feature

Source:
- commit `bdc72ec3`

Behavior:
- plugin hooks fire on session finalize
- plugin hooks fire on session reset

### 5.1.2 Problem Solved

Plugins currently lack explicit session lifecycle boundaries. This limits:
- cleanup
- flush
- final summarization
- session-scoped state reset

### 5.1.3 Product Value

This gives plugins a real session lifecycle. It enables:
- memory/session plugins to flush at correct boundaries
- SG/session-aware plugins to close out a turn cleanly
- future ContextEngine-aware plugins to reset per-session state correctly

### 5.1.4 Integration Design

Primary files:
- [`/Users/wzh/IsacHermes/cli.py`](/Users/wzh/IsacHermes/cli.py)
- [`/Users/wzh/IsacHermes/hermes_cli/plugins.py`](/Users/wzh/IsacHermes/hermes_cli/plugins.py)

Design:
- keep upstream hook names:
  - `on_session_finalize`
  - `on_session_reset`
- do not invent fork-specific names
- trigger points:
  - before `/new`
  - before `/reset` if distinct
  - during orderly CLI exit
  - after new session creation

### 5.1.5 ContextEngine Fit

This is not a request bucket feature. It belongs to lifecycle control.

However, it should be documented as the sanctioned place for future plugins to:
- flush ContextEngine-adjacent state
- snapshot SG summaries
- clear request-scoped sidecars

No new request metric bucket is needed.

### 5.1.6 Test Plan

Add:
- [`/Users/wzh/IsacHermes/tests/test_session_boundary_hooks.py`](/Users/wzh/IsacHermes/tests/test_session_boundary_hooks.py)

Cover:
- finalize hook fires on `/new`
- finalize hook fires on CLI exit path
- reset hook fires after new session creation
- one failing hook does not break CLI flow
- multiple hooks preserve execution order

### 5.1.7 Risk

Low. Mostly lifecycle plumbing.

---

## 5.2 Self-Optimized GPT/Codex Tool-Use Guidance

### 5.2.1 Upstream Feature

Source:
- commit `c8a5e36b`

Behavior:
- stronger OpenAI/Codex execution discipline
- explicit mandatory tool-use categories
- “act, do not ask” guidance for obvious interpretations

### 5.2.2 Problem Solved

Models still sometimes:
- answer from memory instead of tools
- ask unnecessary clarifications
- stop after partial progress
- hallucinate system state, time, hashes, or file facts

### 5.2.3 Product Value

This directly improves agent correctness.

It strengthens the quality of:
- CLI use
- API use
- tool-driven tasks
- multi-step autonomous execution

This complements our existing work:
- upstream prompt discipline improves tool selection and action
- our ContextEngine improves post-tool governance and observability

### 5.2.4 Integration Design

Primary file:
- [`/Users/wzh/IsacHermes/agent/prompt_builder.py`](/Users/wzh/IsacHermes/agent/prompt_builder.py)

Design:
- preserve our existing `TOOL_USE_ENFORCEMENT_GUIDANCE`
- absorb upstream additions into a structured OpenAI/Codex-specific guidance block
- keep this block under ContextEngine-managed sources rather than scattering it

Preferred implementation:
- extend prompt-builder constants with:
  - `<mandatory_tool_use>`
  - `<act_dont_ask>`
  - optional prerequisite-check wording if still useful
- continue exposing this through existing ContextEngine source plumbing:
  - `tool_use_enforcement`
  - `tool_guidance`

### 5.2.5 ContextEngine Fit

This is a first-class ContextEngine fit.

No new source is required if we reuse:
- `tool_guidance`
- `tool_use_enforcement`

But we should enrich source metadata/comments so the new guidance is clearly categorized as:
- model-specific behavioral enforcement

No new request bucket is needed.

### 5.2.6 Test Plan

Add:
- [`/Users/wzh/IsacHermes/tests/context_engine/test_tool_use_guidance.py`](/Users/wzh/IsacHermes/tests/context_engine/test_tool_use_guidance.py)
- [`/Users/wzh/IsacHermes/tests/test_prompt_builder_tool_guidance.py`](/Users/wzh/IsacHermes/tests/test_prompt_builder_tool_guidance.py)

Cover:
- GPT/Codex models include strengthened sections
- non-target models do not get the extra guidance
- existing guidance is preserved
- ContextEngine source labeling remains stable

### 5.2.7 Risk

Medium. Prompt changes can shift behavior. Must validate with focused regression tests.

---

## 5.3 MCP Structured Content Preservation

### 5.3.1 Upstream Feature

Source:
- commits `2ad76948`, `b9a5e6e2`

Behavior:
- preserve MCP structured payload
- prefer structured content when available

### 5.3.2 Problem Solved

Some MCP tools return real machine-usable data in structured fields, while text content is only a human summary.

Without preserving it, Hermes silently loses the most useful payload.

### 5.3.3 Product Value

This improves correctness and downstream usability of MCP tool results:
- better agent reasoning over MCP output
- fewer lossy transformations
- more faithful tool results

### 5.3.4 Integration Design

Primary file:
- [`/Users/wzh/IsacHermes/tools/mcp_tool.py`](/Users/wzh/IsacHermes/tools/mcp_tool.py)

Design:
- reuse upstream behavior and field naming as much as possible
- preserve `structuredContent` in the returned JSON payload
- do not throw away textual `content`
- if both exist, return both

### 5.3.5 ContextEngine Fit

This feature affects tool result quality, not source selection.

ContextEngine impact:
- persisted or shaped tool messages must preserve the structured field if it is present
- tool compaction must never silently drop `structuredContent`
- persistence preview generation should continue to operate on rendered content, but original structured payload should remain preserved in the raw tool result message prior to shaping

Phase 1 rule:
- preserve the structured field in runtime result
- do not yet expose a separate `structured_tool_result` bucket

### 5.3.6 Test Plan

Add:
- [`/Users/wzh/IsacHermes/tests/tools/test_mcp_structured_content.py`](/Users/wzh/IsacHermes/tests/tools/test_mcp_structured_content.py)
- [`/Users/wzh/IsacHermes/tests/context_engine/test_tool_result_structured_content.py`](/Users/wzh/IsacHermes/tests/context_engine/test_tool_result_structured_content.py)

Cover:
- structured payload preserved in tool result JSON
- text payload still preserved
- compaction does not strip structured fields
- persistence preview path does not crash when structured fields are present

### 5.3.7 Risk

Low to medium. Need to avoid breaking existing MCP result expectations.

---

## 5.4 `no_mcp` Sentinel

### 5.4.1 Upstream Feature

Source:
- commit `7fe6782a`

Behavior:
- platform toolset config can explicitly exclude all MCP servers with `no_mcp`

### 5.4.2 Problem Solved

Some platforms should not receive the full MCP tool surface:
- API server backends
- lightweight gateway contexts
- cost-sensitive tool surfaces

Without an exclusion sentinel, MCP schemas may inflate the request dramatically.

### 5.4.3 Product Value

This improves:
- tool surface control
- token efficiency
- platform-specific safety and simplicity

### 5.4.4 Integration Design

Primary files:
- [`/Users/wzh/IsacHermes/hermes_cli/tools_config.py`](/Users/wzh/IsacHermes/hermes_cli/tools_config.py)
- [`/Users/wzh/IsacHermes/model_tools.py`](/Users/wzh/IsacHermes/model_tools.py)
- possibly [`/Users/wzh/IsacHermes/hermes_cli/setup.py`](/Users/wzh/IsacHermes/hermes_cli/setup.py) if surfaced in setup flows

Design:
- adopt the upstream sentinel string exactly: `no_mcp`
- treat it as config-only syntax, never a real toolset
- filter MCP server injection at the toolset resolution stage

### 5.4.5 ContextEngine Fit

This directly affects request-time `tool_schemas`.

ContextEngine impact:
- request metrics must reflect the reduced `tool_schemas` footprint naturally
- no special bucket is required
- this is tool-surface governance, not message shaping

### 5.4.6 Test Plan

Add:
- [`/Users/wzh/IsacHermes/tests/hermes_cli/test_tools_config_no_mcp.py`](/Users/wzh/IsacHermes/tests/hermes_cli/test_tools_config_no_mcp.py)
- [`/Users/wzh/IsacHermes/tests/test_model_tools_no_mcp.py`](/Users/wzh/IsacHermes/tests/test_model_tools_no_mcp.py)

Cover:
- `no_mcp` excludes all MCP servers for a platform
- non-MCP toolsets remain unchanged
- sentinel does not appear in final toolset output
- request metrics show smaller `tool_schemas` when `no_mcp` is active

### 5.4.7 Risk

Low. Mostly config semantics.

---

## 5.5 Jittered Retry Backoff

### 5.5.1 Upstream Feature

Source:
- commit `e1befe50`

Behavior:
- exponential backoff with jitter
- used in agent retry paths and compressor retry paths

### 5.5.2 Problem Solved

Concurrent sessions currently risk synchronized retries against rate-limited providers.

This creates herd behavior and lowers recovery success under load.

### 5.5.3 Product Value

This improves runtime stability in real multi-session environments:
- gateways
- API server
- high-concurrency CLI use

### 5.5.4 Integration Design

Primary files:
- new [`/Users/wzh/IsacHermes/agent/retry_utils.py`](/Users/wzh/IsacHermes/agent/retry_utils.py)
- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)
- [`/Users/wzh/IsacHermes/trajectory_compressor.py`](/Users/wzh/IsacHermes/trajectory_compressor.py)

Design:
- reuse upstream jitter helper nearly verbatim if possible
- replace existing fixed exponential retry waits in:
  - none-choice retry
  - API error retry
  - compression summary retry paths

### 5.5.5 ContextEngine Fit

No direct request bucket impact.

Indirect value:
- keeps request assembly/compression flows stable under provider errors

No metric bucket needed.

### 5.5.6 Test Plan

Add:
- [`/Users/wzh/IsacHermes/tests/test_retry_utils.py`](/Users/wzh/IsacHermes/tests/test_retry_utils.py)

Update:
- retry-related tests in [`/Users/wzh/IsacHermes/tests/test_run_agent.py`](/Users/wzh/IsacHermes/tests/test_run_agent.py)
- compressor retry tests if present

Cover:
- deterministic bounds
- cap enforcement
- monotonic growth
- no negative delays
- call sites actually use jitter helper

### 5.5.7 Risk

Low. Mostly runtime robustness.

---

## 5.6 Anthropic Thinking Block Signature Management

### 5.6.1 Upstream Feature

Source:
- commit `1368caf6`

Behavior:
- removes or downgrades stale Anthropic thinking blocks that would fail signature validation
- retries once on signature-specific error

### 5.6.2 Problem Solved

Long-lived Anthropic sessions can fail with:
- `Invalid signature in thinking block`

This is triggered by message mutations such as:
- compression
- session truncation
- orphan sanitization
- consecutive assistant merge behavior

### 5.6.3 Product Value

This is a stability feature:
- fewer Anthropic hard failures in long sessions
- preserved reasoning continuity on recent turns
- better recovery behavior

### 5.6.4 Integration Design

Primary files:
- [`/Users/wzh/IsacHermes/agent/anthropic_adapter.py`](/Users/wzh/IsacHermes/agent/anthropic_adapter.py)
- [`/Users/wzh/IsacHermes/run_agent.py`](/Users/wzh/IsacHermes/run_agent.py)

Design:
- adopt upstream strategy, but adapt it to our sanitizer and shaping pipeline
- preserve newest valid reasoning continuity
- proactively strip stale reasoning blocks from older assistant turns for Anthropic payload construction
- keep one-shot error recovery for signature-specific failures

### 5.6.5 ContextEngine Fit

This affects transport-safe request construction rather than semantic source selection.

ContextEngine impact:
- no new request metric bucket
- request metrics should remain based on final payload after Anthropic adaptation
- if reasoning blocks are removed for transport safety, metrics should reflect final transport-visible messages, not the pre-adaptation semantic snapshot

### 5.6.6 Test Plan

Add or port:
- [`/Users/wzh/IsacHermes/tests/agent/test_anthropic_adapter.py`](/Users/wzh/IsacHermes/tests/agent/test_anthropic_adapter.py)

Cover:
- old signed thinking blocks are stripped
- unsigned thinking blocks are downgraded safely
- cache_control is not preserved where it invalidates signatures
- merge-time assistant alternation does not keep stale thinking
- signature-specific 400 triggers one-shot cleanup retry

### 5.6.7 Risk

Medium to high. This touches subtle provider-specific reasoning behavior.

## 6. Delivery Order

### Phase 1: Low-risk, high-value runtime correctness
1. MCP structured content preservation
2. Session boundary hooks
3. Jittered retry backoff

### Phase 2: Tool surface and prompt discipline
4. `no_mcp` sentinel
5. GPT/Codex tool-use guidance strengthening

### Phase 3: Provider-specific long-session hardening
6. Anthropic thinking block signature management

## 7. Implementation Rules

For every phase:

1. Prefer upstream code when it is already well-scoped
2. Keep runtime behavior and ContextEngine accounting consistent
3. Do not add undocumented side effects to the request path
4. Do not bypass the final request metrics pipeline
5. Add tests before or alongside integration, never as a later cleanup step

## 8. Coverage Matrix

Each feature must be covered at all applicable layers:

### 8.1 Unit
- pure helper behavior
- field preservation
- config parsing
- retry math

### 8.2 Integration
- real agent request path
- transport adapter path
- CLI/plugin lifecycle path
- MCP/tool execution path

### 8.3 ContextEngine / Metrics
- final request bucket correctness
- sidecar alignment
- no metadata leakage to provider payload

### 8.4 Regression
- `tests/context_engine/`
- `tests/tools/`
- `tests/test_run_agent.py`
- `tests/test_run_agent_codex_responses.py`
- full `tests/`

### 8.5 Cross-Feature Combination Tests

At least one integration test must exist for each high-risk feature interaction:

- MCP structured content + tool persistence
- MCP structured content + tool compaction
- `no_mcp` + request metrics / `tool_schemas` accounting
- Anthropic transport adaptation + final request metrics
- strengthened tool-use guidance + existing tool-use enforcement sources

These combination tests are mandatory. Single-feature unit coverage alone is insufficient.

## 9. Hard Acceptance Gates

The following are non-optional implementation gates. No feature in this roadmap should be considered complete unless all applicable gates pass.

### Gate 1: Structured Payload Preservation

`structuredContent` must never be silently dropped by:
- tool result normalization
- tool persistence
- tool compaction
- final request assembly

If a reduction path cannot preserve it faithfully, that path must explicitly fall back rather than degrade silently.

### Gate 2: Final-Payload Metrics Fidelity

Final request metrics must always reflect the final transport-visible payload, not an earlier semantic snapshot.

This is especially required for:
- Anthropic thinking block stripping/downgrade
- sanitizer-inserted tool stubs
- persisted tool previews
- any transport-specific message mutation

### Gate 3: Hook Failure Isolation

Plugin lifecycle hooks must never be allowed to break the primary CLI/session flow.

Required behavior:
- hook exceptions are caught
- failures are logged
- remaining hooks still run
- session finalize/reset behavior still completes

### Gate 4: No Over-Tooling Regression

Prompting improvements for tool-use discipline must not introduce obvious over-tooling regressions.

At minimum we must verify:
- normal conversational prompts remain tool-free when tools add no value
- obvious factual/runtime/tool-required prompts become more reliably tool-driven
- the new guidance does not duplicate or conflict with existing enforcement blocks

### Gate 5: Combination-Test Requirement

Every roadmap feature must have:
- standalone tests for its direct behavior
- at least one combination test with another relevant layer or feature

Examples:
- session hooks + CLI reset flow
- `no_mcp` + ContextEngine tool schema metrics
- Anthropic signature cleanup + request breakdown

No feature ships on single-layer tests alone.

## 10. Success Criteria

This roadmap is considered successfully executed when:

1. All six features are either:
   - integrated, or
   - explicitly deferred with a written architectural reason
2. Upstream persistence is documented as already absorbed
3. All request-affecting behavior is reflected correctly in ContextEngine metrics
4. Full test suite passes
5. No integrated feature exists only as runtime behavior without observability or test coverage

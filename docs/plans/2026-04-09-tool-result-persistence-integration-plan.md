# Tool Result Persistence Integration Plan

## Goal

Integrate the upstream Hermes `tool result persistence` design into our fork
without discarding our existing ContextEngine request-governance work.

The target end state is:

1. Large tool outputs are reduced **before** they become long-lived `role="tool"`
   messages.
2. Reduction prefers **persisting full output to sandbox + preview in context**
   over destructive truncation.
3. Our existing ContextEngine remains the single place that:
   - classifies tool history into `hot / warm / cold`
   - measures request-time context usage
   - renders request breakdown in CLI
4. The plan explicitly records that we have absorbed the upstream solution, so
   future work does not reopen the same design question.


## Decision Record

### Upstream capability being adopted

We are explicitly adopting the upstream `3-layer tool result persistence`
approach from official Hermes `main`, centered on:

- `tools/budget_config.py`
- `tools/tool_result_storage.py`

The adopted upstream concepts are:

1. Per-result threshold:
   - very large tool results are persisted to sandbox storage
   - request context receives a preview + path reference
2. Per-turn aggregate budget:
   - if the sum of tool outputs in a single assistant turn exceeds budget,
     additional large results are persisted until under budget
3. Configurable thresholds:
   - default threshold
   - turn budget
   - preview size
   - per-tool override support

### Fork-specific capability being retained

We are explicitly **not** replacing our existing request-governance layer.

We retain:

- `ToolGroup`
- request-time `shape_tool_history()`
- `messages_tool_hot`
- `messages_tool_warm`
- `messages_tool_cold`
- final request-level metrics and CLI display

### Final architecture decision

The integrated architecture is layered:

1. Tool author/tool implementation:
   - may still do local output caps
2. Tool result persistence layer:
   - upstream mechanism
   - runs immediately after tool execution
3. ContextEngine request shaping layer:
   - our mechanism
   - runs when preparing model input

This settles the design question for future work:

- **persistence solves preservation**
- **ContextEngine solves governance and observability**


## Why This Integration

Our current `msg:tool` compaction is effective, but it is still fundamentally
destructive:

- head-tail truncation loses middle content
- large outputs still first enter conversation history
- quality depends on later request shaping

Upstream persistence improves this materially:

- the full output is preserved
- the context holds a compact preview instead of raw bulk text
- the model can recover detail through `read_file`

This means our current system becomes:

- safer for information retention
- cheaper in context
- easier to explain and debug


## Scope

### In scope

- Adopt upstream persistence primitives
- Thread them through current `run_agent.py` tool execution flow
- Expose persistence state to ContextEngine metrics
- Preserve sanitizer correctness
- Preserve existing `msg:tool` shaping behavior where still needed
- Add regression and integration tests

### Out of scope for this phase

- Rewriting tool schemas
- Replacing our hot/warm/cold system
- Provider-specific payload optimizations
- Sandboxed artifact lifecycle cleanup beyond minimum safety
- New retrieval UI for persisted files


## Current System Summary

Today the flow is effectively:

1. tool executes
2. raw tool output becomes `role="tool"` message
3. message stays in history
4. request-time shaping compresses older tool content
5. metrics classify the shaped tool content

The weakness is step 2:

- raw bulk output already enters the conversation history
- later compaction is forced to rescue the situation


## Target System Summary

The new flow should be:

1. tool executes
2. persistence layer evaluates the raw result
3. if oversized:
   - save full output into sandbox-accessible file
   - generate preview block for in-context message
4. all tool messages for the assistant turn are collected
5. aggregate turn budget may persist additional large results
6. only then are finalized tool messages appended to history
7. ContextEngine shapes and measures the finalized message view


## Detailed Design

## 1. New Low-Level Primitives

### 1.1 `tools/budget_config.py`

Add an upstream-style immutable config object:

- `BudgetConfig`
- `DEFAULT_BUDGET`
- `DEFAULT_RESULT_SIZE_CHARS`
- `DEFAULT_TURN_BUDGET_CHARS`
- `DEFAULT_PREVIEW_SIZE_CHARS`
- `PINNED_THRESHOLDS`

Required behavior:

- support global default threshold
- support per-turn aggregate budget
- support preview size
- support per-tool overrides
- support pinned tools that must never persist

Required initial pinned thresholds:

- `read_file = inf`

Rationale:

- prevents persist -> read_file -> persist loops

### 1.2 `tools/tool_result_storage.py`

Add upstream-style helper module with these responsibilities:

- decide whether a result should persist
- write full content into sandbox via current environment
- build preview block for context
- enforce aggregate per-turn budget

Required functions:

- `generate_preview(content, max_chars)`
- `_write_to_sandbox(content, remote_path, env)`
- `_build_persisted_message(preview, has_more, original_size, file_path)`
- `maybe_persist_tool_result(...)`
- `enforce_turn_budget(...)`

Required constants:

- `PERSISTED_OUTPUT_TAG`
- `PERSISTED_OUTPUT_CLOSING_TAG`
- `STORAGE_DIR`


## 2. Registry Changes

### 2.1 `tools/registry.py`

We need a supported way to resolve per-tool persistence thresholds.

Add to `ToolEntry`:

- `max_result_size`

Update `register(...)` to accept:

- `max_result_size: Optional[int | float] = None`

Add helper:

- `get_max_result_size(name: str, default: int | float) -> int | float`

Behavior:

- if tool has explicit override, return it
- else return default

This is required so the upstream persistence mechanism is not reimplemented
via ad-hoc tool-name conditionals.


## 3. Runtime Integration Points

### 3.1 Sequential tool execution

Modify:

- `/Users/wzh/IsacHermes/run_agent.py`
- `_execute_tool_calls_sequential(...)`

Current behavior:

- tool handler returns `function_result`
- `role="tool"` message is appended immediately

Target behavior:

1. capture raw `function_result`
2. normalize to string exactly as today
3. run `maybe_persist_tool_result(...)`
4. stage tool message in a turn-local list
5. after all tool calls in this assistant turn complete:
   - run `enforce_turn_budget(...)` on the staged list
6. append finalized list to `messages`

Important:

- do not append raw tool messages before turn-level budget enforcement

### 3.2 Concurrent tool execution

Modify:

- `/Users/wzh/IsacHermes/run_agent.py`
- `_execute_tool_calls_concurrent(...)`

Target behavior mirrors sequential:

1. execute tools concurrently
2. collect raw outputs
3. apply per-result persistence to each result
4. assemble all tool messages for the turn
5. apply aggregate turn budget
6. append finalized tool messages in stable order

Stable order requirement:

- final tool message order must remain aligned with original tool call order

### 3.3 Error and interrupted paths

All places in `run_agent.py` that append synthetic tool results must be reviewed.

This includes:

- invalid tool name recovery
- invalid JSON recovery
- interrupted/skipped tool calls
- error recovery when assistant tool_calls already exist
- sanitizer-injected fallback result paths

Rule:

- synthetic short tool results do **not** need persistence
- but they must remain compatible with the new turn-local staging path


## 4. ContextEngine Integration

### 4.1 Message shaping order

Current required invariant stays in force:

- shaping must happen before preflight estimate

New required order:

1. tool execution creates finalized persisted-preview messages
2. these messages enter conversation history
3. request shaping runs on this finalized message view
4. sanitizer runs
5. final request metrics are computed from sanitized final payload

### 4.2 Heat sidecar

Current invariant stays in force:

- heat metadata must remain sidecar-only

New persistence metadata must follow the same rule:

- no internal persistence classification fields may leak into provider payload

Recommended sidecar additions:

- `message_heat_by_index`
- `message_persistence_by_index`

Allowed persistence states:

- `inline`
- `persisted_preview`
- `persisted_budget`

### 4.3 Metrics changes

Update:

- `/Users/wzh/IsacHermes/agent/context_engine/request_metrics.py`
- `/Users/wzh/IsacHermes/cli.py`

We need visibility into persistence adoption.

Recommended additional buckets:

- `messages_tool_persisted`
- retain existing:
  - `messages_tool_hot`
  - `messages_tool_warm`
  - `messages_tool_cold`

Interpretation:

- `messages_tool_persisted` answers:
  - how much tool content was reduced by persistence
- hot/warm/cold continue to answer:
  - how the finalized tool message view is retained across request shaping

### 4.4 ToolGroup shaping behavior

Update:

- `/Users/wzh/IsacHermes/agent/context_engine/tool_groups.py`
- `/Users/wzh/IsacHermes/agent/context_engine/tool_compaction.py`

Rule changes:

1. persisted-preview messages should be considered already reduced
2. shaping may still classify them as hot/warm/cold
3. but shaping should avoid aggressively head-tail truncating an already
   persisted preview unless absolutely necessary

Priority order:

1. reduce raw oversized tool outputs via persistence
2. only then apply request-time shaping


## 5. Configuration Design

### 5.1 Add new config section

Add under:

- `agent.context_engine.tool_persistence`

Recommended keys:

- `enabled: true`
- `default_result_size_chars: 100000`
- `turn_budget_chars: 200000`
- `preview_size_chars: 1500`
- `tool_overrides: {}`

### 5.2 Keep existing compaction config

Do not remove current:

- `agent.context_engine.tool_compaction.*`

The systems solve different layers.

### 5.3 Parameter defaults

Use upstream defaults first to avoid inventing a second standard:

- `default_result_size_chars = 100000`
- `turn_budget_chars = 200000`
- `preview_size_chars = 1500`

Reason:

- preserves alignment with upstream semantics
- reduces future duplicate design work


## 6. Storage Semantics

### 6.1 Sandbox path

Use the upstream path convention initially:

- `/tmp/hermes-results/{tool_use_id}.txt`

Reason:

- matches the official design
- easy for model to consume via `read_file`
- minimizes divergence

### 6.2 Write method

Use active environment execution rather than local filesystem writes:

- `env.execute(...)`

Reason:

- must work across local / docker / ssh / modal / daytona / singularity

### 6.3 Failure fallback

If sandbox write fails:

- fallback to inline truncation preview
- log warning
- preserve tool message validity

Do not fail the tool call itself only because persistence failed.


## 7. Boundaries and Edge Cases

This section is normative. Implementation is not complete unless these cases are
tested or explicitly shown to be inherited safely.

### 7.1 Output-shape boundaries

- empty tool content
- very short content
- non-string tool content normalized to string
- multiline content
- binary-looking content already represented as text
- extremely large content
- content containing heredoc delimiter text

### 7.2 Turn-shape boundaries

- single tool call in turn
- multiple sequential tool calls
- multiple concurrent tool calls
- a turn with many medium-size results exceeding aggregate budget
- a turn where latest results are small but early result is huge

### 7.3 Tool-category boundaries

- `read_file` must not be re-persisted
- tools that already self-truncate
- tools returning structured JSON text
- browser/search/file/terminal outputs
- synthetic tool error messages

### 7.4 Conversation-flow boundaries

- first turn with tool usage
- long single-turn tool loops
- multi-turn history with persisted previews
- `/compress`
- retry after model/provider failure
- interrupted tool execution
- skipped tool calls
- continuation turns

### 7.5 API/sanitizer boundaries

- orphan tool messages
- sanitizer-injected stub tool results
- strict provider message normalization
- final metrics after sanitize
- sidecar alignment after deletions and insertions

### 7.6 Environment boundaries

- local env
- docker env
- ssh env
- modal/daytona/singularity-like envs
- env unavailable / missing
- env.execute returns non-zero

### 7.7 Recovery and persistence boundaries

- persisted file path collision
- repeated tool_call_id fallback generation
- sandbox write partially succeeds then fails
- read-after-persist path readability
- restart after persisted outputs already existed on disk


## 8. Test Plan

This plan must land with strong coverage. No phase is complete without tests.

## 8.1 New unit tests

### `tests/tools/test_budget_config.py`

Cover:

- default config values
- pinned threshold resolution
- per-tool override precedence
- default fallback precedence

### `tests/tools/test_tool_result_storage.py`

Cover:

- preview generation
- newline-aware truncation
- heredoc marker collision handling
- persistence success path
- persistence fallback path when env missing
- persistence fallback path when write fails
- aggregate turn budget reducing large results first
- already-persisted messages skipped by budget enforcement
- `read_file` pinned infinite threshold

## 8.2 Run-agent integration tests

### `tests/test_run_agent_tool_result_persistence.py`

Cover:

- sequential tool execution persistence
- concurrent tool execution persistence
- final message order stability
- synthetic tool errors remain valid
- interrupt path does not break staged message append

### `tests/test_run_agent_tool_result_budget.py`

Cover:

- one large result over single-result threshold
- many medium results over turn budget
- budget enforcement after per-result pass

## 8.3 ContextEngine tests

### `tests/context_engine/test_tool_persistence_metrics.py`

Cover:

- persisted-preview classification in request metrics
- persisted-preview survives into final metrics after sanitize
- `messages_tool_persisted` bucket accuracy
- coexistence with `hot/warm/cold`

### `tests/context_engine/test_tool_persistence_shaping.py`

Cover:

- persisted previews are not over-compressed too early
- raw oversized messages are preferred persistence candidates
- shaped message view remains deterministic

### `tests/context_engine/test_tool_persistence_sidecar.py`

Cover:

- sidecar metadata stays out of provider payload
- sanitize deletion/insertion preserves metric alignment

## 8.4 Environment tests

### `tests/tools/test_tool_result_persistence_envs.py`

Cover:

- local environment write path
- mock docker/ssh env write path
- env.execute failure fallback

## 8.5 Regression tests

Must rerun at minimum:

- `tests/context_engine/`
- `tests/sparkgraph/`
- `tests/tools/`
- `tests/test_run_agent.py`
- `tests/test_run_agent_codex_responses.py`
- full `tests/`


## 9. Implementation Phases

## Phase A — Introduce official persistence primitives

Files:

- `tools/budget_config.py`
- `tools/tool_result_storage.py`
- `tools/registry.py`

Acceptance:

- unit tests for thresholds and persistence helpers pass
- no runtime integration yet

## Phase B — Integrate into tool execution flow

Files:

- `run_agent.py`

Acceptance:

- per-result persistence works in sequential and concurrent tool execution
- turn budget enforcement applies before tool messages are appended
- no payload/schema regressions

## Phase C — Integrate with ContextEngine metrics

Files:

- `agent/context_engine/request_metrics.py`
- `agent/context_engine/tool_groups.py`
- `agent/context_engine/tool_compaction.py`
- `cli.py`

Acceptance:

- persisted preview states are visible in metrics
- sidecar remains internal-only
- sanitizer alignment tests pass

## Phase D — Regression hardening

Files:

- tests only, plus bugfixes discovered during integration

Acceptance:

- full test suite passes
- real session verification confirms:
  - lower `msg:tool`
  - no payload metadata leakage
  - no broken tool message pairing


## 10. Release Impact Assessment

### Positive impact

- lower prompt token pressure from oversized tool outputs
- better preservation of complete information
- less reliance on destructive request-time truncation
- clearer observability into what was persisted vs. merely compressed

### Behavioral impact

- tool messages for large outputs will change format
- model may invoke `read_file` more often
- this is intended and should be monitored

### Risk areas

- environment write reliability
- path/reference usability for the model
- persistence-preview wording quality
- sidecar/metrics alignment after sanitizer


## 11. Explicit Non-Goals for This Work

To avoid repeated solution drift, this work explicitly does **not** include:

- reworking tool schemas
- replacing ContextEngine shaping with persistence only
- removing hot/warm/cold
- provider-specific prompt-format optimization
- semantic summarization of tool outputs


## 12. Final Recommendation

Proceed with integration.

This is the recommended synthesis:

- adopt the official persistence layer as the new lowest-level defense
- preserve our ContextEngine governance and metrics above it
- standardize config and storage semantics on upstream defaults where possible

This gives us:

- the upstream-preserved full-output story
- our stronger request-level governance and observability

and prevents future duplicate handling of the same `tool output bloat` problem.

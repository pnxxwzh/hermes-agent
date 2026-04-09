# Upstream Gap Triage

Date: 2026-04-09
Branch: `spark-main`
Upstream snapshot: `upstream-temp/main @ 989d4ea4`
Comparison window: upstream commits after previously reviewed baseline `ff6a86cb`

## Scope

This document tracks the **26 upstream non-merge commits** that landed after the
last systematic upstream review. It is intentionally limited to the newest
delta so we can make release-quality decisions without re-triaging the entire
historical divergence from upstream.

## Status Summary

### Integrated now

1. `7d26feb9` `feat(discord): add DISCORD_REPLY_TO_MODE setting`
   - Added configurable Discord reply-reference behavior:
     - `off`
     - `first`
     - `all`
   - Value:
     - improves Discord thread UX
     - reduces noisy reply chains
     - aligns Discord with existing platform-specific reply controls
   - Merge status:
     - absorbed via cherry-pick with low-risk conflict resolution in docs and the
       Discord adapter
   - Local result:
     - commit `271c4d1e`

2. `4f467700` `fix(doctor): only check the active memory provider`
   - Fixes false doctor failures when inactive memory providers are configured
     but not actually selected.
   - Also includes the browser-side managed persistence cleanup fix that avoids
     destroying Camofox sessions during cleanup.
   - Value:
     - `hermes doctor` becomes trustworthy again for multi-provider configs
     - browser persistence no longer loses login state on cleanup
   - Merge status:
     - absorbed via cherry-pick with targeted adaptation
   - Why adaptation was needed:
     - upstream assumes the newer generic `plugins.memory.*` provider stack
     - this repo still uses `honcho_integration`
   - Local result:
     - commit `f16e743b`

### Requires adaptation

1. `980fadfe` `fix(models): preserve OpenRouter variant tags`
   - Function:
     - prevents OpenRouter model ids like `vendor/model:free` from being
       mangled during model switching
   - Value:
     - avoids 400s from invalid model ids
     - preserves exact billing / tier suffixes
   - Why not direct:
     - our `hermes_cli/model_switch.py` is materially simpler than upstream’s
       rewritten catalog-aware switch pipeline
     - the exact buggy conversion path from upstream is not present in the same
       form locally
   - Recommended action:
     - implement a targeted regression test first, then patch local
       `parse_model_input()` / switch behavior only if the bug reproduces
   - Risk:
     - medium; a naive cherry-pick would entangle our local switch logic with
       upstream’s newer alias/provider resolver architecture

2. `ae4a884e` `fix(agent): disable stale stream timeout for local providers`
   - Function:
     - stops local providers from being killed while they are still prefilling
       large contexts
   - Value:
     - materially improves local model stability
     - reduces false timeouts for Ollama / llama.cpp / similar endpoints
   - Why not direct:
     - lives in `run_agent.py`, where we have heavy local customization:
       ContextEngine request assembly, metrics, persistence, Anthropic handling
   - Recommended action:
     - adapt the local-endpoint detection to our current stale-stream logic
       without regressing cloud-provider behavior
   - Risk:
     - medium/high; timeout handling is intertwined with streaming safety and
       error recovery

3. `42e366f2` `fix(agent): respect config timeout for flush_memories`
   - Function:
     - makes `flush_memories` use configured timeouts rather than hardcoded 30s
   - Value:
     - improves consistency and prevents premature memory flush failures
   - Why not direct:
     - also lives in our heavily customized `run_agent.py`
   - Recommended action:
     - small adaptation patch with regression tests for both auxiliary and
       direct fallback code paths
   - Risk:
     - medium; low conceptual risk, but must be wired into our current timeout
       helper usage

4. `875a72e4` `fix: normalize httpx.URL base_url + strip thinking signatures for third-party endpoints`
   - Function:
     - normalizes `httpx.URL` base_url objects to strings
     - strips Anthropic thinking signatures when talking to third-party
       Anthropic-compatible endpoints that cannot validate them
   - Value:
     - prevents crashes on `httpx.URL.rstrip()`
     - improves MiniMax / proxy / Anthropic-compatible endpoint stability
   - Why not direct:
     - directly overlaps with our recently integrated Anthropic thinking
       signature management and transport-visible metrics accounting
   - Recommended action:
     - adapt into our current `agent/anthropic_adapter.py` and final payload
       metrics path
   - Risk:
     - high; easy to fix endpoint behavior while silently regressing
       metrics/payload alignment

5. `09206171` `fix(gateway): add staged inactivity warning before timeout escalation`
   - Function:
     - adds an early warning before gateway inactivity reaches full timeout
   - Value:
     - better user experience on long/slow tasks
     - gives users a chance to intervene before reset/timeout
   - Why not direct:
     - touches gateway timeout behavior, messaging UX, and config
   - Recommended action:
     - adapt into our current gateway timeout flow and platform-specific
       follow-up behavior
   - Risk:
     - medium; low logic complexity, but easy to create duplicate or noisy
       warnings across platforms

6. `3377017e` `feat(qwen): add Qwen OAuth provider`
   - Function:
     - adds a new OAuth-based provider with portal request support
   - Value:
     - meaningful provider expansion
     - useful for users already invested in Qwen’s auth flow
   - Why not direct:
     - touches auth, provider resolution, runtime provider, status, and model
       selection
   - Recommended action:
     - treat as a provider feature project, not a trivial upstream sync
   - Risk:
     - medium/high; auth/provider surface is already locally customized

7. `5d2fc6d9` `fix: cleanup Qwen OAuth provider gaps`
   - Function:
     - follow-up fixes for the new Qwen OAuth provider
   - Value:
     - only meaningful if `3377017e` is adopted
   - Recommended action:
     - bundle with Qwen OAuth adoption work
   - Risk:
     - tied to the provider feature above

### Defer for now

1. `d684d7ee` `feat(environments): unified spawn-per-call execution layer`
2. `e19252af` `fix: update tests for unified spawn-per-call execution model`
   - Function:
     - large execution-layer redesign replacing mixed persistent/oneshot shell
       handling with a unified spawn-per-call model plus session snapshotting
   - Value:
     - cleaner backend abstraction
     - more uniform behavior across local/docker/ssh/modal/daytona/singularity
   - Why defer:
     - this is effectively a subsystem refactor, not a patch
     - it would collide with our current terminal, code execution, process, and
       environment behavior assumptions
   - Risk if adopted now:
     - very high regression surface across tools and background execution
   - Risk if deferred:
     - we continue carrying our current execution model and miss some upstream
       consistency improvements

2. `5f4b93c2` `feat(tools): add Voxtral Transcribe STT provider`
3. `d46db0a1` `fix(tools): use correct import path for mistralai SDK`
   - Function:
     - adds an additional STT provider via Mistral/Voxtral
   - Value:
     - expands voice transcription options
   - Why defer:
     - useful, but not a correctness fix for the current product line
   - Risk if adopted now:
     - moderate dependency/config growth for a non-critical feature
   - Risk if deferred:
     - no new STT option; current transcription path remains unchanged

3. `a1213d06` `fix(hindsight): correct config key mismatch and add base URL support`
   - Function:
     - fixes the Hindsight memory integration configuration
   - Value:
     - only matters if Hindsight is in active use
   - Why defer:
     - outside our current primary memory stack
   - Risk if adopted now:
     - low, but it adds maintenance surface to a path we are not prioritizing
   - Risk if deferred:
     - Hindsight users keep existing limitations

4. `8de91ce9` and `8385f54e` Nix fixes
   - Function:
     - improve Nix packaging behavior
   - Value:
     - important for Nix users
   - Why defer:
     - limited impact on the current release path
   - Risk if adopted now:
     - low/medium packaging churn without broad user impact
   - Risk if deferred:
     - Nix-specific UX remains rough

### Not feature work / no action needed right now

1. `989d4ea4` test-only mock fix
2. `af4abd2f` narrow bugfix to warning math / exception variable
3. `6e3f7f36` docs only
4. `20a5e589` docs only
5. `7156f8d8` mixed CI/test cleanup
6. `105caa00` lockfile refresh
7. `1631895d` docs only

### Already effectively covered by the integrated doctor/browser cleanup commit

1. `3baafea3` `fix(tools): skip camofox auto-cleanup when managed persistence is enabled`
   - This browser cleanup behavior arrived as part of the `4f467700` cherry-pick
     we already absorbed locally.

## Recommended Next Integration Order

1. `980fadfe` OpenRouter variant tag preservation
2. `42e366f2` flush_memories timeout correctness
3. `ae4a884e` local-provider stale stream timeout fix
4. `875a72e4` Anthropic-compatible endpoint signature stripping
5. `09206171` gateway staged inactivity warning
6. `3377017e` + `5d2fc6d9` Qwen OAuth provider

## Acceptance Rules

For every adaptation item above:

1. Do not port upstream architecture wholesale if the local subsystem has
   diverged materially.
2. Add a regression test reproducing the upstream bug before patching.
3. Run targeted subsystem tests and at least one broader regression suite.
4. Preserve transport-visible request metrics whenever the change touches
   message shaping, Anthropic adaptation, or runtime payloads.

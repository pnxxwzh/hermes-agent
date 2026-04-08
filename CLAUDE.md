# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Hermes Agent** — self-improving AI agent built by Nous Research. Creates skills from experience, improves them during use, persistent memory across sessions, supports multiple messaging platforms.

## Setup

```bash
# Using uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"

# Standard pip
pip install -e ".[all,dev]"
```

## Commands

```bash
hermes              # Interactive CLI
hermes model        # Choose LLM provider and model
hermes tools        # Configure enabled tools
hermes gateway      # Start messaging gateway (Telegram, Discord, etc.)
hermes setup        # Full setup wizard
hermes update       # Update to latest version
hermes doctor       # Diagnose issues

# Testing
python -m pytest tests/ -q                          # Full suite (~3000 tests, ~3 min)
python -m pytest tests/test_model_tools.py -q     # Toolset resolution
python -m pytest tests/gateway/ -q                # Gateway tests
python -m pytest tests/tools/ -q                  # Tool-level tests
```

## Architecture

### Core Components

```
run_agent.py        # AIAgent class — core conversation loop
model_tools.py      # Tool discovery and function call handling
toolsets.py         # Toolset definitions (_HERMES_CORE_TOOLS)
cli.py              # HermesCLI class — interactive terminal UI
hermes_state.py     # SessionDB — SQLite session store (FTS5 search)
agent/              # Agent internals (prompt builder, context compression, etc.)
hermes_cli/         # CLI subcommands and setup
tools/              # Tool implementations (registry-based, 55+ tools)
gateway/            # Multi-platform messaging gateway
```

### Tool Registration Flow

```
tools/registry.py  (central registry — no deps)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry, triggers tool discovery)
       ↑
run_agent.py, cli.py, batch_runner.py
```

### Adding New Tools (3 files)

1. **Create `tools/your_tool.py`** — register with `registry.register()`, handler returns JSON string
2. **Add import** in `model_tools.py` `_discover_tools()` list
3. **Add to `toolsets.py`** — either `_HERMES_CORE_TOOLS` or a new toolset

### Adding Slash Commands (2 files)

1. Add `CommandDef` to `COMMAND_REGISTRY` in `hermes_cli/commands.py`
2. Add handler in `HermesCLI.process_command()` in `cli.py`

### Key Architectural Patterns

- **Profile isolation**: Use `get_hermes_home()` from `hermes_constants` for all file paths (never `~/.hermes` or `Path.home() / ".hermes"`)
- **Prompt caching**: Never alter past context or reload toolsets mid-conversation
- **Tool handlers**: All MUST return a JSON string
- **Gateway platforms**: Use token locks (`acquire_scoped_lock()`/`release_scoped_lock()`) for credential-based connections

### User Config

- Config: `~/.hermes/config.yaml`
- API keys: `~/.hermes/.env`
- Profiles: `~/.hermes/profiles/<name>/`

## Development Guide

The `AGENTS.md` file in the root contains comprehensive development documentation including:
- AIAgent class internals and agent loop
- CLI architecture and slash command system
- Skin/theme system
- Profiles (multi-instance) support
- Known pitfalls
- Testing instructions

**Read `AGENTS.md` before making significant changes** — it has detailed patterns for adding tools, commands, and configuration that are authoritative for this codebase.

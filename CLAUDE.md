# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

**Setup:**
- `uv sync --all-extras` - Install all dependencies including dev tools

**Essential Commands (use these exact commands):**
- `uv run poe format` - Format code (BLACK + RUFF) - ONLY allowed formatting command
- `uv run poe type-check` - Run mypy type checking - ONLY allowed type checking command
- `uv run poe test` - Run tests with default markers (excludes rust/erlang by default)
- `uv run poe lint` - Check code style without fixing

**Running Specific Tests:**
- `uv run poe test -m "python or go"` - Run tests for specific languages
- `uv run pytest test/solidlsp/python/test_python_basic.py -v` - Run single test file
- `uv run pytest test/solidlsp/python/test_python_basic.py::test_find_symbol -v` - Run single test function

**Test Markers:** `python`, `go`, `java`, `rust`, `typescript`, `php`, `perl`, `csharp`, `elixir`, `terraform`, `clojure`, `swift`, `bash`, `ruby`, `zig`, `lua`, `nix`, `dart`, `scala`, `al`, `rego`, `markdown`, `julia`, `fortran`, `haskell`, `yaml`, `snapshot`

**Starting the Server:**
- `uv run serena-mcp-server` - Start MCP server (CLI entry point: `src/serena/cli.py`)

**Always run format, type-check, and test before completing any task.**

## Architecture Overview

Serena is a dual-layer coding agent toolkit exposing IDE-like capabilities via MCP (Model Context Protocol).

### Core Components

**SerenaAgent** (`src/serena/agent.py`) - Central orchestrator managing projects, tools, language servers, and memory persistence. Exposes tools via MCP server (`src/serena/mcp.py`).

**SolidLanguageServer** (`src/solidlsp/ls.py`) - Unified synchronous wrapper around 30+ LSP implementations. Provides language-agnostic symbol operations with caching and error recovery. Key abstraction: wraps async LSP communication in synchronous Python API.

**Tool System** (`src/serena/tools/`) - MCP tools inheriting from `Tool` base class in `tools_base.py`. Tools are registered via decorators and filtered by context/mode configuration.

**Configuration System** (`src/serena/config/`) - YAML-based configs in `src/serena/resources/config/`:
- **Contexts** (`contexts/`) - Tool sets for environments (ide-assistant, agent, desktop-app)
- **Modes** (`modes/`) - Operational patterns (interactive, editing, planning, one-shot)

### Language Support

Each language requires:
1. Language server class in `src/solidlsp/language_servers/`
2. Entry in `Language` enum in `src/solidlsp/ls_config.py`
3. Test repo in `test/resources/repos/<language>/` and tests in `test/solidlsp/<language>/`
4. Pytest marker in `pyproject.toml`

### Memory System

Markdown files in `.serena/memories/` directories provide persistent project knowledge across sessions.

## Development Patterns

### Adding New Tools
1. Subclass `Tool` in `src/serena/tools/tools_base.py`
2. Implement `apply` method with typed parameters
3. Add to context/mode YAML configs as needed

### Testing
- Language tests use pytest markers (run specific language: `uv run poe test -m python`)
- Snapshot tests for symbolic editing: `uv run poe test -m snapshot`
- Integration tests: `test/serena/test_serena_agent.py`

## Configuration Hierarchy

Configuration precedence (highest to lowest):
1. CLI arguments to `serena-mcp-server`
2. Project-specific `.serena/project.yml`
3. User config `~/.serena/serena_config.yml`
4. Default contexts and modes

## Key Implementation Notes

- **Python 3.11 only** (strict version requirement in pyproject.toml)
- **Synchronous LSP wrapper** - SolidLanguageServer wraps async LSP in sync API for simpler tool implementation
- **Language servers as subprocesses** - Each runs in separate process, communication via JSON-RPC
- **Symbol-based editing** - Prefer `replace_symbol_body`, `insert_after_symbol` over line-based edits

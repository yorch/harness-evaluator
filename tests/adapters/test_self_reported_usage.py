"""Tests for adapter parse_self_reported_usage() methods."""

from __future__ import annotations

import json

from harness_evaluator.adapters.base import BaseAdapter
from harness_evaluator.adapters.claude_code import ClaudeCodeAdapter
from harness_evaluator.adapters.codex import CodexAdapter
from harness_evaluator.adapters.omp import OMPAdapter
from harness_evaluator.adapters.opencode import OpenCodeAdapter
from harness_evaluator.adapters.pi import PiAdapter
from harness_evaluator.orchestrator.config import ModelSpec


def _anthropic_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


def _openai_model() -> ModelSpec:
    return ModelSpec(
        name="gpt-4o",
        provider="openai",
        api_key_env="OPENAI_API_KEY",
    )


class TestBaseAdapterDefault:
    def test_base_returns_none(self, tmp_path):
        """BaseAdapter.parse_self_reported_usage returns None by default."""

        class DummyAdapter(BaseAdapter):
            @staticmethod
            def info():
                from harness_evaluator.adapters.base import AdapterInfo

                return AdapterInfo(
                    name="dummy",
                    display_name="Dummy",
                    observability_tier="minimal",
                    description="dummy",
                )

            async def prepare(self):
                pass

            async def run(self, task_prompt, timeout=600):
                pass

        adapter = DummyAdapter(workdir=str(tmp_path), model=_anthropic_model())
        assert adapter.parse_self_reported_usage("some output", "") is None


class TestClaudeCodeSelfReportedUsage:
    def test_parse_json_with_usage(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        stdout = json.dumps(
            {
                "type": "result",
                "num_turns": 3,
                "session_id": "abc",
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 800,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 100,
                },
            }
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 1500
        assert usage.output_tokens == 800
        assert usage.cache_write_tokens == 200
        assert usage.cache_read_tokens == 100

    def test_parse_with_ansi_escape(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        payload = json.dumps(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            }
        )
        stdout = f"\x1B[32m{payload}\x1B[0m"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_returns_none_for_non_json(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        assert adapter.parse_self_reported_usage("plain text output", "") is None

    def test_returns_none_for_empty_stdout(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        assert adapter.parse_self_reported_usage("", "") is None

    def test_returns_none_when_no_usage_field(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        stdout = json.dumps({"type": "result", "num_turns": 1})
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_returns_none_when_all_zero(self, tmp_path):
        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        stdout = json.dumps(
            {"usage": {"input_tokens": 0, "output_tokens": 0}}
        )
        assert adapter.parse_self_reported_usage(stdout, "") is None


class TestCodexSelfReportedUsage:
    def test_parse_json_usage_in_stdout(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stdout = (
            "Some codex output\n"
            '{"input_tokens": 500, "output_tokens": 300}\n'
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 500
        assert usage.output_tokens == 300

    def test_parse_json_usage_in_stderr(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stderr = '{"input_tokens": 200, "output_tokens": 100}'
        usage = adapter.parse_self_reported_usage("", stderr)
        assert usage is not None
        assert usage.input_tokens == 200
        assert usage.output_tokens == 100

    def test_parse_with_reasoning_tokens(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        # Codex uses reasoning_output_tokens and cached_input_tokens.
        stdout = (
            '{"input_tokens": 100, "output_tokens": 50, '
            '"reasoning_output_tokens": 75, "cached_input_tokens": 20}'
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.reasoning_tokens == 75
        assert usage.cache_read_tokens == 20

    def test_returns_none_for_no_usage(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        assert adapter.parse_self_reported_usage("no tokens here", "") is None

    def test_returns_none_for_empty(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        assert adapter.parse_self_reported_usage("", "") is None

    def test_returns_none_when_all_zero(self, tmp_path):
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stdout = '{"input_tokens": 0, "output_tokens": 0}'
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_parse_jsonl_with_nested_usage(self, tmp_path):
        """Codex exec --json emits JSONL with nested usage objects."""
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stdout = (
            '{"type":"turn.started","session_id":"abc"}\n'
            '{"type":"turn.completed","usage":'
            '{"input_tokens":26549,"cached_input_tokens":22272,'
            '"output_tokens":1590,"reasoning_output_tokens":300}}\n'
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 26549
        assert usage.output_tokens == 1590
        assert usage.cache_read_tokens == 22272
        assert usage.reasoning_tokens == 300

    def test_parse_ansi_wrapped_jsonl(self, tmp_path):
        """Codex output may include ANSI escape sequences."""
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stdout = (
            "\x1b[32m{\"type\":\"turn.completed\","
            "\"usage\":{\"input_tokens\":100,\"output_tokens\":50}}"
            "\x1b[0m\n"
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_ignores_unrelated_json(self, tmp_path):
        """JSON objects without token fields should not match."""
        adapter = CodexAdapter(
            workdir=str(tmp_path), model=_openai_model()
        )
        stdout = (
            '{"type":"file.write","path":"foo.py","content":"x = 1"}\n'
            '{"config":{"model":"gpt-4o"}}\n'
        )
        assert adapter.parse_self_reported_usage(stdout, "") is None


class TestOpenCodeSelfReportedUsage:
    def test_parse_json_usage_in_stdout(self, tmp_path):
        adapter = OpenCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        stdout = (
            "opencode result\n"
            '{"input_tokens": 400, "output_tokens": 250}\n'
        )
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 400
        assert usage.output_tokens == 250

    def test_parse_json_usage_in_stderr(self, tmp_path):
        adapter = OpenCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        stderr = '{"input_tokens": 100, "output_tokens": 80}'
        usage = adapter.parse_self_reported_usage("", stderr)
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 80

    def test_returns_none_for_no_usage(self, tmp_path):
        adapter = OpenCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        assert adapter.parse_self_reported_usage("no tokens", "") is None

    def test_returns_none_for_empty(self, tmp_path):
        adapter = OpenCodeAdapter(
            workdir=str(tmp_path), model=_anthropic_model()
        )
        assert adapter.parse_self_reported_usage("", "") is None


class TestPiAndOMPSelfReportedUsage:
    """Pi and OMP have minimal observability and do not report usage."""

    def test_pi_returns_none(self, tmp_path):
        adapter = PiAdapter(workdir=str(tmp_path), model=_anthropic_model())
        assert adapter.parse_self_reported_usage("some output", "") is None

    def test_omp_returns_none(self, tmp_path):
        adapter = OMPAdapter(workdir=str(tmp_path), model=_anthropic_model())
        assert adapter.parse_self_reported_usage("some output", "") is None

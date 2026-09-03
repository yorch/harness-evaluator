"""Tests for the streaming output callback in _run_subprocess."""

from __future__ import annotations

import asyncio

import pytest

from harness_evaluator.runner.docker import _run_subprocess


class TestRunSubprocessStreaming:
    def test_no_callback_returns_full_output(self) -> None:
        """Without on_output, the original communicate() path is used."""
        result = asyncio.run(
            _run_subprocess(["python3", "-c", "print('hello'); print('world')"])
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert "world" in result.stdout

    def test_with_callback_returns_full_output(self) -> None:
        """With on_output, the full output is still captured for the return value."""
        chunks: list[tuple[str, bytes]] = []

        def on_output(stream: str, data: bytes) -> None:
            chunks.append((stream, data))

        result = asyncio.run(
            _run_subprocess(
                ["python3", "-c", "print('hello'); print('world')"],
                on_output=on_output,
            )
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert "world" in result.stdout
        # The callback should have received at least one stdout chunk
        stdout_chunks = [d for s, d in chunks if s == "stdout"]
        assert any(b"hello" in d for d in stdout_chunks)

    def test_callback_receives_stderr(self) -> None:
        chunks: list[tuple[str, bytes]] = []

        def on_output(stream: str, data: bytes) -> None:
            chunks.append((stream, data))

        result = asyncio.run(
            _run_subprocess(
                ["python3", "-c", "import sys; sys.stderr.write('err msg\\n')"],
                on_output=on_output,
            )
        )
        assert result.returncode == 0
        assert "err msg" in result.stderr
        stderr_chunks = [d for s, d in chunks if s == "stderr"]
        assert any(b"err msg" in d for d in stderr_chunks)

    def test_callback_exception_does_not_fail_subprocess(self) -> None:
        """A callback that raises must not fail the subprocess or lose output."""

        def bad_callback(stream: str, data: bytes) -> None:
            raise RuntimeError("callback exploded")

        result = asyncio.run(
            _run_subprocess(
                ["python3", "-c", "print('survived')"],
                on_output=bad_callback,
            )
        )
        assert result.returncode == 0
        assert "survived" in result.stdout

    def test_timeout_with_callback(self) -> None:
        """Timeout still works with the streaming path."""
        import subprocess

        def on_output(stream: str, data: bytes) -> None:
            pass

        with pytest.raises(subprocess.TimeoutExpired):
            asyncio.run(
                _run_subprocess(
                    ["python3", "-c", "import time; time.sleep(10)"],
                    timeout=1,
                    on_output=on_output,
                )
            )

    def test_large_output_streamed_and_captured(self) -> None:
        """Large output is both streamed and fully captured."""
        chunks: list[bytes] = []

        def on_output(stream: str, data: bytes) -> None:
            if stream == "stdout":
                chunks.append(data)

        result = asyncio.run(
            _run_subprocess(
                ["python3", "-c", "for i in range(1000): print(f'line {i}')"],
                on_output=on_output,
            )
        )
        assert result.returncode == 0
        assert "line 0" in result.stdout
        assert "line 999" in result.stdout
        # The streamed chunks should reconstruct the full output
        streamed = b"".join(chunks).decode()
        assert "line 0" in streamed
        assert "line 999" in streamed

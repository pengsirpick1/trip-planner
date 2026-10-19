"""Hard guard used by all default benchmarks.

The guard blocks socket connects and makes construction of the project's real
OpenAI-compatible client an error.  Benchmark fakes never import production
bootstrap, so keys present in a developer's .env cannot be used accidentally.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager


class OfflineViolation(RuntimeError):
    pass


@contextmanager
def offline_guard():
    old_connect = socket.socket.connect
    old_create_connection = socket.create_connection
    os.environ["BENCHMARK_OFFLINE"] = "1"

    def blocked(*_args, **_kwargs):
        raise OfflineViolation("offline benchmark attempted an external network connection")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    try:
        from app.providers.openai_compatible import OpenAICompatibleToolCallingLLM

        old_init = OpenAICompatibleToolCallingLLM.__init__

        def blocked_llm(*_args, **_kwargs):
            raise OfflineViolation("offline benchmark attempted to instantiate a real LLM client")

        OpenAICompatibleToolCallingLLM.__init__ = blocked_llm
        try:
            yield
        finally:
            OpenAICompatibleToolCallingLLM.__init__ = old_init
    finally:
        socket.socket.connect = old_connect
        socket.create_connection = old_create_connection


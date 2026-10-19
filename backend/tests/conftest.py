"""backend 测试基础设施（默认全离线）。

隔离策略（吸取 travel_agent 只 patch urllib 的教训）：
- 阻断 urllib.request.urlopen。
- 阻断 socket 层连接（覆盖未来 httpx/requests 直连与 MCP 子进程外联的兜底）。
- 生产装配由 create_app(settings) 显式注入测试 Settings；tests/fakes 仅供测试注入，
  生产 bootstrap 不得 import 本目录。
- online 测试必须 @pytest.mark.online，且默认跳过（RUN_ONLINE_TESTS != '1'）。
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _offline_blocker(monkeypatch):
    if os.getenv("RUN_MYSQL_TESTS") == "1":
        yield
        return
    """默认阻断所有外联：urllib + socket 两层兜底。"""

    def deny(*_args, **_kwargs):
        raise RuntimeError("网络在离线测试中被禁用；真实效果评估请显式 opt-in (RUN_ONLINE_TESTS=1)")

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    # 拦 httpx/requests 的直连入口；不替换 socket.socket 本体（Windows asyncio
    # 事件循环内部依赖它做 self-pipe，整体替换会破坏测试进程）。
    monkeypatch.setattr(socket, "create_connection", deny)
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "online: explicitly opt-in external-provider tests")


@pytest.fixture
def workdir():
    """沙箱可写、不做删除清理的临时目录（沙箱禁用目录删除/回收站）。

    注意：不要用 pytest 的 tmp_path（其基目录/清理在受限 Windows 沙箱下会触发
    safe-delete 失败）。
    """
    import uuid

    base = Path(os.getenv("PYTEST_WORKDIR", str(BACKEND_DIR / ".pytest-tmp")))
    base.mkdir(parents=True, exist_ok=True)
    folder = base / ("run-" + uuid.uuid4().hex[:12])
    folder.mkdir()
    return folder


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_ONLINE_TESTS") == "1":
        return
    skip_online = pytest.mark.skip(reason="online 测试需 RUN_ONLINE_TESTS=1 显式开启")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)

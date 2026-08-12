"""C 工作线：Java 内部调用的委托用户身份（X-Agent-User-Id）。

委托头由 worker 从会话身份（系统信道）写入，与 body 里模型可见的 userId 分离。
这里验证 client 的 header 行为与 contextvar 的 task 隔离；Java 侧的一致性/归属
校验由 OrderAgentInternalControllerTest 覆盖。
"""

import asyncio

from app.services.java_internal_client import (
    clear_delegated_user_id,
    java_internal_client,
    set_delegated_user_id,
)


def test_headers_omit_delegation_when_unset():
    clear_delegated_user_id()
    headers = java_internal_client._headers()
    assert "X-Agent-User-Id" not in headers


def test_headers_carry_delegated_user_id():
    set_delegated_user_id("u1")
    headers = java_internal_client._headers()
    assert headers["X-Agent-User-Id"] == "u1"
    clear_delegated_user_id()


def test_set_trims_and_ignores_empty():
    set_delegated_user_id("  u2  ")
    assert java_internal_client._headers()["X-Agent-User-Id"] == "u2"
    set_delegated_user_id("")
    assert "X-Agent-User-Id" not in java_internal_client._headers()
    set_delegated_user_id(None)
    assert "X-Agent-User-Id" not in java_internal_client._headers()
    clear_delegated_user_id()


async def test_delegation_is_isolated_per_task():
    """并发消费不同用户的消息时，各自的 java_internal 调用只带自己的委托身份。"""

    async def consume(user_id: str) -> str:
        set_delegated_user_id(user_id)
        await asyncio.sleep(0.02)  # 让两个任务交错
        return java_internal_client._headers().get("X-Agent-User-Id")

    results = await asyncio.gather(consume("alice"), consume("bob"))
    assert sorted(results) == ["alice", "bob"]
    clear_delegated_user_id()

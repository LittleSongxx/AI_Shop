"""让测试跑在确定的配置上，而不是跑在"我这台机器的 .env"上。

起因是一个真实的坑：`Settings` 的 `model_config` 写了 `env_file=".env"`，而 pytest 通常
从仓库根目录启动，于是**测试读的是开发者本地那份 .env**。后果有两层：

1. 断言默认值的测试（如 `test_memory_llm_falls_back_to_chat_config` 断言 timeout==60）
   会因为本地 .env 里配了别的值而变红，而代码完全没问题；
2. 反过来更糟——本地 .env 恰好配对了，测试就绿，但换台机器/CI 上就红。
   这种测试的绿色不携带任何信息。

而且这条路径不是显式的：`app/rag/retriever.py` 这类模块在 import 时就创建单例并调
`get_settings()`，所以配置在"pytest 收集测试"阶段就已经被读进来了。因此这里的处理必须
发生在模块 import 期（conftest 先于测试模块被 import），放进 fixture 就太晚了。

保留 shell 显式导出的变量：`tests/integration/test_migrations.py` 用 `os.getenv` 直读
MYSQL_*，并且由 `RUN_AGENT_MIGRATION_TESTS=1` 门控。往 shell 里导变量是运行测试的人有意
为之，和"仓库目录里躺着一份 .env"完全不同，不该被这里抹掉。
"""

from __future__ import annotations

import os

import pytest

from app.config.settings import Settings, get_settings
from app.services.redis_service import redis_service

# 集成测试自己用 os.getenv 直读的变量，不属于 Settings 的管辖范围，原样放过。
_PASSTHROUGH_PREFIXES = ("MYSQL_", "RUN_AGENT_")


def _settings_env_names() -> set[str]:
    """列出 Settings 会读的所有环境变量名。

    从模型字段和 validation_alias 推导，而不是手写一张表——手写的表一定会和字段定义漂移，
    这正是本次审计里反复出现的那类 bug。
    """
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        names.add(name.upper())
        alias = field.validation_alias
        for choice in getattr(alias, "choices", []) or ([alias] if alias else []):
            if isinstance(choice, str):
                names.add(choice.upper())
    return names


def _isolate_settings_from_dotenv() -> None:
    # 不读 .env 文件。测试要覆盖配置就用 monkeypatch.setenv，显式且限定作用域。
    Settings.model_config["env_file"] = None

    passthrough = tuple(_PASSTHROUGH_PREFIXES)
    for name in _settings_env_names():
        if name.startswith(passthrough):
            continue
        os.environ.pop(name, None)

    # import 期可能已经有人取过 Settings（模块级单例），旧值必须作废。
    get_settings.cache_clear()


_isolate_settings_from_dotenv()


async def _allow_test_nonce(_nonce_hash: str, _ttl_seconds: int) -> bool:
    return True


# Unit route tests do not start the production Redis lifespan. Individual replay tests
# replace this function with a stateful fake.
redis_service.claim_admin_assertion_nonce = _allow_test_nonce


@pytest.fixture(autouse=True)
def _restore_process_state_between_tests():
    """Keep process-wide settings/runtime helpers from leaking across tests.

    The evaluator deliberately derives service URLs by writing ``os.environ``
    because it runs as a standalone process.  Unit tests call that same entry
    point directly, so a plain ``monkeypatch`` cannot see those writes.  A
    snapshot at the test boundary makes the test suite deterministic while
    leaving the production process-level behavior unchanged.
    """
    environment_before = os.environ.copy()
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key in list(os.environ):
            if key not in environment_before:
                os.environ.pop(key, None)
        for key, value in environment_before.items():
            os.environ[key] = value
        get_settings.cache_clear()

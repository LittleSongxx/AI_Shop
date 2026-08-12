import pytest

from app.config.settings import Settings


def test_qwen3_rerank_is_the_default_configuration():
    settings = Settings()

    assert settings.rerank_model == "qwen3-rerank"
    assert settings.rerank_api_format == "compatible"
    assert settings.rerank_base_url == ""


def test_dedicated_rerank_key_takes_precedence_over_dashscope_key(monkeypatch):
    monkeypatch.setenv("RERANK_API_KEY", "dedicated-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shared-key")
    monkeypatch.setenv("RERANK_BASE_URL", "https://workspace.example.test/reranks")

    settings = Settings(_env_file=None)

    assert settings.rerank_api_key == "dedicated-key"


def test_blank_key_allows_a_workspace_placeholder_for_initial_local_setup():
    settings = Settings(
        rerank_api_key="",
        rerank_base_url=(
            "https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/"
            "compatible-api/v1/reranks"
        ),
    )

    assert settings.rerank_api_key == ""


def test_configured_key_rejects_a_workspace_placeholder():
    with pytest.raises(ValueError, match="workspace placeholder"):
        Settings(
            rerank_api_key="configured-key",
            rerank_base_url=(
                "https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/"
                "compatible-api/v1/reranks"
            ),
        )


@pytest.mark.parametrize("url", ["", "workspace-only", "ftp://workspace.example.test/reranks"])
def test_configured_key_requires_an_absolute_http_url(url):
    with pytest.raises(ValueError, match="RERANK_BASE_URL"):
        Settings(rerank_api_key="configured-key", rerank_base_url=url)


def test_configured_key_requires_a_model():
    with pytest.raises(ValueError, match="RERANK_MODEL"):
        Settings(
            rerank_api_key="configured-key",
            rerank_base_url="https://workspace.example.test/reranks",
            rerank_model="",
        )

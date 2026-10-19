"""Phase A：配置统一（test_config）。

覆盖：旧变量兼容映射（setdefault + 命中列表）、新旧冲突报错、新 Settings 字段映射、
必需配置缺失检测、不依赖跨项目 .env 加载。
"""

from __future__ import annotations

import pytest

from app.config import (
    Settings,
    apply_deprecated_env_mappings,
    validate_core_config,
)


def test_deprecated_mapping_applies_and_reports(monkeypatch):
    env = {"LLM_MODEL_ID": "legacy-model"}
    applied = apply_deprecated_env_mappings(env, warn=lambda *a, **k: None)
    assert applied == ["LLM_MODEL_ID"]
    assert env["LLM_MODEL"] == "legacy-model"
    # 已存在新变量且值相同 → 不覆盖、不冲突，仅报告命中
    env2 = {"LLM_MODEL": "same-model", "LLM_MODEL_ID": "same-model"}
    applied2 = apply_deprecated_env_mappings(env2, warn=lambda *a, **k: None)
    assert applied2 == ["LLM_MODEL_ID"]
    assert env2["LLM_MODEL"] == "same-model"


def test_deprecated_mapping_conflict_raises(monkeypatch):
    env = {"LLM_MODEL": "new-model", "LLM_MODEL_ID": "different-legacy"}
    with pytest.raises(RuntimeError, match="配置冲突"):
        apply_deprecated_env_mappings(env, warn=lambda *a, **k: None)


def test_settings_reads_unified_env_names(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key-a")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "model-a")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    settings = Settings()
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "key-a"
    assert settings.llm_base_url == "https://llm.invalid/v1"
    assert settings.llm_model == "model-a"
    assert settings.llm_timeout_seconds == 45.0
    assert settings.llm_configured is True


def test_settings_old_name_maps_after_module_apply(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_MODEL_ID", "legacy-1")
    monkeypatch.setenv("LLM_API_KEY", "key-legacy")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.invalid/v1")
    applied = apply_deprecated_env_mappings()
    assert "LLM_MODEL_ID" in applied
    settings = Settings()
    assert settings.llm_model == "legacy-1"


def test_validate_core_config_missing_items():
    settings = Settings(llm_api_key=None, llm_base_url=None, llm_model=None, auth_required=False)
    missing = validate_core_config(settings)
    assert set(missing) == {"LLM_API_KEY 未配置", "LLM_BASE_URL 未配置", "LLM_MODEL 未配置"}


def test_validate_core_config_full_ok():
    settings = Settings(
        llm_api_key="k", llm_base_url="https://llm.invalid/v1", llm_model="m", auth_required=False
    )
    assert validate_core_config(settings) == []


def test_resolve_db_path_relative_to_backend():
    settings = Settings(db_path="data/runs.sqlite")
    resolved = settings.resolve_db_path()
    assert resolved.is_absolute()
    assert resolved.name == "runs.sqlite"
    assert "data" in resolved.parts


def test_production_requires_mysql_database_url():
    settings = Settings(
        app_env="production", llm_api_key="k", llm_base_url="https://llm.invalid/v1",
        llm_model="m", auth_secret_key="production-test-secret-long-enough"
    )
    assert "APP_ENV=production 时必须配置 DATABASE_URL" in validate_core_config(settings)

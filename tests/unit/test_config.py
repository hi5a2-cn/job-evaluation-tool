from pathlib import Path
from jet.config import DEFAULT_DATA_DIR, load_settings
from jet.llm.versions import DEFAULT_PROMPT_VERSION


def test_config_defaults(tmp_path: Path):
    empty_dir = tmp_path / "empty_dir"
    settings = load_settings(data_dir=empty_dir, env={})

    assert settings.data_dir == empty_dir
    assert settings.port == 47615
    assert settings.daily_llm_limit == 150
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_api_key is None
    assert settings.llm_timeout_s == 20.0
    assert settings.price_input_per_m == 2.0
    assert settings.price_cached_per_m == 0.04
    assert settings.price_output_per_m == 8.0
    assert settings.judge_concurrency == 3
    assert settings.prompt_version == DEFAULT_PROMPT_VERSION
    # Does not create empty_dir
    assert not empty_dir.exists()


def test_data_dir_priority(tmp_path: Path, monkeypatch):
    param_dir = tmp_path / "param_dir"
    env_dir = tmp_path / "env_dir"
    default_dir = tmp_path / "default_dir"
    monkeypatch.setattr("jet.config.DEFAULT_DATA_DIR", default_dir)

    # 1. Parameter wins over env and default
    s1 = load_settings(data_dir=param_dir, env={"JET_DATA_DIR": str(env_dir)})
    assert s1.data_dir == param_dir

    # 2. Env wins over default
    s2 = load_settings(data_dir=None, env={"JET_DATA_DIR": str(env_dir)})
    assert s2.data_dir == env_dir

    # 3. Default when neither is provided
    s3 = load_settings(data_dir=None, env={})
    assert s3.data_dir == default_dir

    # 证明在第 3 步期间读取的是临时目录
    default_dir.mkdir(parents=True, exist_ok=True)
    (default_dir / ".env").write_text("JET_DAILY_LLM_LIMIT=7\n", encoding="utf-8")
    s3 = load_settings(data_dir=None, env={})
    assert s3.daily_llm_limit == 7


def test_read_env_file(tmp_path: Path):
    target_dir = tmp_path / "data_with_env"
    target_dir.mkdir()
    env_file = target_dir / ".env"
    env_file.write_text(
        """# Comment line
JET_PORT=48000
JET_LLM_MODEL=deepseek-chat
JET_LLM_API_KEY="sk-test-12345"
JET_DAILY_LLM_LIMIT=100
""",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=target_dir, env={})
    assert settings.port == 48000
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_api_key == "sk-test-12345"
    assert settings.daily_llm_limit == 100


def test_env_precedence_over_file(tmp_path: Path):
    target_dir = tmp_path / "data_with_env"
    target_dir.mkdir()
    env_file = target_dir / ".env"
    env_file.write_text("JET_PORT=48000\nJET_DAILY_LLM_LIMIT=80\n", encoding="utf-8")

    settings = load_settings(
        data_dir=target_dir,
        env={"JET_PORT": "49000"},
    )
    # JET_PORT from env overrides file
    assert settings.port == 49000
    # JET_DAILY_LLM_LIMIT from file is used
    assert settings.daily_llm_limit == 80


def test_daily_limit_from_env_and_validation(data_dir):
    import pytest as _pytest

    from jet.config import load_settings

    (data_dir).mkdir(parents=True, exist_ok=True)
    (data_dir / ".env").write_text("JET_DAILY_LLM_LIMIT=200\n", encoding="utf-8")
    assert load_settings(data_dir, env={}).daily_llm_limit == 200

    (data_dir / ".env").write_text("JET_DAILY_LLM_LIMIT=900\n", encoding="utf-8")
    with _pytest.raises(ValueError, match="0–500"):
        load_settings(data_dir, env={})

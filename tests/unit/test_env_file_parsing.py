from pathlib import Path

from jet.config import load_settings


def test_env_file_trailing_comments_numeric_and_string(tmp_path: Path):
    """行尾注释（数字型和字符串型变量各一个）。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JET_DAILY_LLM_LIMIT=150  # 判断上限\n"
        "JET_LLM_MODEL=deepseek-chat  # 模型名称\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.daily_llm_limit == 150
    assert settings.llm_model == "deepseek-chat"


def test_env_file_hash_without_whitespace_preserved(tmp_path: Path):
    """值中间不带空白的 # 保留。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JET_LLM_MODEL=custom#model#name\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.llm_model == "custom#model#name"


def test_env_file_hash_inside_quotes_preserved(tmp_path: Path):
    """引号内的 # 保留。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        'JET_LLM_API_KEY="sk-123#456"\n'
        "JET_LLM_MODEL='deepseek#chat'\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.llm_api_key == "sk-123#456"
    assert settings.llm_model == "deepseek#chat"


def test_env_file_quoted_value_with_trailing_comment(tmp_path: Path):
    """引号值后带注释。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        'JET_LLM_API_KEY="sk-123#456"  # api key with hash\n'
        "JET_LLM_MODEL='deepseek-chat'  # model in single quotes\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.llm_api_key == "sk-123#456"
    assert settings.llm_model == "deepseek-chat"


def test_env_file_empty_value_uses_default_port(tmp_path: Path):
    """JET_PORT= 空值时使用默认端口 47615。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JET_PORT=\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.port == 47615


def test_env_file_whole_line_comment(tmp_path: Path):
    """整行注释。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# JET_PORT=48000\n"
        "# 这是一个完整注释行\n"
        "JET_DAILY_LLM_LIMIT=120\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.port == 47615
    assert settings.daily_llm_limit == 120


def test_env_file_old_syntax_without_comments_unchanged(tmp_path: Path):
    """没有注释的旧写法结果不变。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JET_PORT=48000\n"
        "JET_LLM_MODEL=deepseek-chat\n"
        'JET_LLM_API_KEY="sk-test-12345"\n'
        "JET_DAILY_LLM_LIMIT=100\n",
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path, env={})
    assert settings.port == 48000
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_api_key == "sk-test-12345"
    assert settings.daily_llm_limit == 100

import os
from pathlib import Path
import pytest

from jet.config import (
    LLM_KEY_FILE_NAME,
    Settings,
    delete_llm_key_file,
    get_llm_key_info,
    load_settings,
    mask_llm_key,
    read_llm_key_from_file,
    save_llm_key_to_file,
)


def test_save_llm_key_creates_file_with_0600_and_atomic_replace(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    fake_key = "sk-test-1234567890abcdef"

    # 1. 首次保存（数据目录尚不存在时自动创建）
    save_llm_key_to_file(data_dir, f"  {fake_key}\n")
    target_file = data_dir / LLM_KEY_FILE_NAME
    assert target_file.is_file()

    # 权限必须为 0600（仅本人可读写）
    stat_mode = target_file.stat().st_mode & 0o777
    assert oct(stat_mode) == "0o600"
    assert target_file.read_text(encoding="utf-8") == fake_key

    # 2. 覆盖保存（原子替换写入），权限仍为 0600
    new_key = "sk-test-9999888877776666"
    save_llm_key_to_file(data_dir, new_key)
    assert target_file.read_text(encoding="utf-8") == new_key
    stat_mode_after = target_file.stat().st_mode & 0o777
    assert oct(stat_mode_after) == "0o600"

    # 3. 空白或纯空格 Key 拒绝保存并抛出 ValueError
    with pytest.raises(ValueError, match="不能为空"):
        save_llm_key_to_file(data_dir, "   ")


def test_delete_llm_key_removes_file(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    save_llm_key_to_file(data_dir, "sk-test-1234")
    target_file = data_dir / LLM_KEY_FILE_NAME
    assert target_file.is_file()

    delete_llm_key_file(data_dir)
    assert not target_file.exists()

    # 重复删除幂等且不报错
    delete_llm_key_file(data_dir)


def test_read_llm_key_from_file(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    data_dir.mkdir()

    # 文件不存在
    assert read_llm_key_from_file(data_dir) is None

    # 文件为空或全空白
    key_file = data_dir / LLM_KEY_FILE_NAME
    key_file.write_text("   \n\t", encoding="utf-8")
    assert read_llm_key_from_file(data_dir) is None

    # 有效文件内容自动 strip
    key_file.write_text("\n  sk-my-secret-key  \n", encoding="utf-8")
    assert read_llm_key_from_file(data_dir) == "sk-my-secret-key"


def test_mask_llm_key():
    fake_key = "sk-test-1234567890abcd"
    masked = mask_llm_key(fake_key)
    assert masked == "••••abcd"
    assert fake_key not in masked
    assert "1234567890" not in masked

    # 短 key
    assert mask_llm_key("abc") == "••••abc"
    assert mask_llm_key("abcd") == "••••abcd"

    # None 与空白
    assert mask_llm_key(None) is None
    assert mask_llm_key("") is None
    assert mask_llm_key("   ") is None


def test_config_priority_file_over_env_and_dotenv(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    data_dir.mkdir()

    # 1. 写入 .env 文件
    (data_dir / ".env").write_text("JET_LLM_API_KEY=sk-dotenv-key\n", encoding="utf-8")

    # 2. 写入独立的 llm_api_key 文件
    save_llm_key_to_file(data_dir, "sk-settings-page-key")

    # 3. 环境变量中也注入
    env = {"JET_LLM_API_KEY": "sk-env-key"}

    settings = load_settings(data_dir=data_dir, env=env)
    assert settings.llm_api_key == "sk-settings-page-key"
    assert settings.llm_key_source == "settings_page"


def test_config_priority_env_over_dotenv_when_no_file(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    data_dir.mkdir()

    (data_dir / ".env").write_text("JET_LLM_API_KEY=sk-dotenv-key\n", encoding="utf-8")
    env = {"JET_LLM_API_KEY": "sk-env-key"}

    settings = load_settings(data_dir=data_dir, env=env)
    assert settings.llm_api_key == "sk-env-key"
    assert settings.llm_key_source == "env"


def test_config_priority_dotenv_when_no_file_and_no_env(tmp_path: Path):
    data_dir = tmp_path / "data_dir"
    data_dir.mkdir()

    (data_dir / ".env").write_text("JET_LLM_API_KEY=sk-dotenv-key\n", encoding="utf-8")

    settings = load_settings(data_dir=data_dir, env={})
    assert settings.llm_api_key == "sk-dotenv-key"
    assert settings.llm_key_source == "env"


def test_config_no_key_configured(tmp_path: Path):
    data_dir = tmp_path / "empty_dir"
    settings = load_settings(data_dir=data_dir, env={})
    assert settings.llm_api_key is None
    assert settings.llm_key_source is None


def test_get_llm_key_info(tmp_path: Path):
    fake_key = "sk-test-1234567890abcd"
    s_settings = Settings(data_dir=tmp_path, llm_api_key=fake_key, llm_key_source="settings_page")
    info = get_llm_key_info(s_settings)
    assert info == {
        "configured": True,
        "source": "settings_page",
        "masked": "••••abcd",
    }
    assert fake_key not in str(info)

    s_env = Settings(data_dir=tmp_path, llm_api_key=fake_key, llm_key_source="env")
    assert get_llm_key_info(s_env)["source"] == "env"

    s_none = Settings(data_dir=tmp_path, llm_api_key=None, llm_key_source=None)
    assert get_llm_key_info(s_none) == {
        "configured": False,
        "source": None,
        "masked": None,
    }

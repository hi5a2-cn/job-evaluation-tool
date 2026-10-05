from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
from typing import Any

from jet.llm.versions import DEFAULT_PROMPT_VERSION, list_prompt_versions

DEFAULT_DATA_DIR = Path.home() / "Library/Application Support/Jet"
LLM_KEY_FILE_NAME = "llm_api_key"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int = 47615
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_api_key: str | None = None
    llm_key_source: str | None = None
    # 每日判断调用上限（含复核）；jet serve 启动时写入数据库。改法：数据目录 .env 里设 JET_DAILY_LLM_LIMIT，重启生效
    daily_llm_limit: int = 150
    # 每日话术生成调用上限；jet serve 启动时写入数据库。改法：数据目录 .env 里设 JET_DAILY_ASSIST_LIMIT，重启生效
    daily_assist_limit: int = 50
    # 初判最大并发数；改法：数据目录 .env 设 JET_JUDGE_CONCURRENCY，重启生效
    judge_concurrency: int = 3
    llm_timeout_s: float = 20.0
    price_input_per_m: float = 2.0
    price_cached_per_m: float = 0.04
    price_output_per_m: float = 8.0
    prompt_version: str = DEFAULT_PROMPT_VERSION
    judge_engine: str = "deepseek-flash:no-think"
    llm_thinking_timeout_s: float = 60.0
    eval_max_calls: int = 300
    # 复核：B 给出 apply / try 时，用开启思考的 C 再判一次，只允许降档（2026-09-25 用户决定）
    review_enabled: bool = True
    review_engine: str = "deepseek-flash:think"


def _parse_env_file(env_file_path: Path) -> dict[str, str]:
    if not env_file_path.is_file():
        return {}
    res: dict[str, str] = {}
    try:
        content = env_file_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue

                raw_val = val
                clean_val = raw_val.strip()

                is_quoted = False
                if len(clean_val) >= 2 and clean_val[0] in ('"', "'"):
                    quote_char = clean_val[0]
                    last_quote = clean_val.rfind(quote_char)
                    if last_quote > 0:
                        after = clean_val[last_quote + 1:]
                        if not after:
                            val = clean_val[1:last_quote]
                            is_quoted = True
                        elif re.match(r"^\s+#", after):
                            val = clean_val[1:last_quote]
                            is_quoted = True

                if not is_quoted:
                    m = re.search(r"\s+#", raw_val)
                    if m:
                        val = raw_val[:m.start()].strip()
                    elif raw_val.lstrip().startswith("#"):
                        val = ""
                    else:
                        val = raw_val.strip()

                if val:
                    res[key] = val
    except OSError:
        pass
    return res


def mask_llm_key(key: str | None) -> str | None:
    """API Key 脱敏：仅展示前置四个点加最后 4 位，未配置返回 None。"""
    if not key:
        return None
    clean = key.strip()
    if not clean:
        return None
    if len(clean) <= 4:
        return f"••••{clean}"
    return f"••••{clean[-4:]}"


def read_llm_key_from_file(data_dir: Path) -> str | None:
    """从数据目录读取独立凭据文件 llm_api_key（0600 权限），仅读不创建。"""
    key_file = data_dir / LLM_KEY_FILE_NAME
    if not key_file.is_file():
        return None
    try:
        content = key_file.read_text(encoding="utf-8").strip()
        return content if content else None
    except OSError:
        return None


def save_llm_key_to_file(data_dir: Path, key: str) -> None:
    """将 API Key 原子写入数据目录 llm_api_key 文件，设置 0600 权限。"""
    clean = key.strip()
    if not clean:
        raise ValueError("API Key 不能为空")
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / LLM_KEY_FILE_NAME
    tmp = data_dir / f"{LLM_KEY_FILE_NAME}.tmp.{secrets.token_hex(8)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(clean)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def delete_llm_key_file(data_dir: Path) -> None:
    """删除数据目录下的 llm_api_key 文件。"""
    key_file = data_dir / LLM_KEY_FILE_NAME
    key_file.unlink(missing_ok=True)


def resolve_env_llm_key(data_dir: Path, env: Mapping[str, str] | None = None) -> str | None:
    """检查环境变量或 .env 中的 JET_LLM_API_KEY。"""
    target_env = env if env is not None else os.environ
    if "JET_LLM_API_KEY" in target_env and target_env["JET_LLM_API_KEY"]:
        val = target_env["JET_LLM_API_KEY"].strip()
        if val:
            return val
    file_env = _parse_env_file(data_dir / ".env")
    if "JET_LLM_API_KEY" in file_env and file_env["JET_LLM_API_KEY"]:
        val = file_env["JET_LLM_API_KEY"].strip()
        if val:
            return val
    return None


def get_llm_key_info(settings: Settings) -> dict[str, Any]:
    """根据当前 Settings 获取 API Key 状态对象 (LlmKeyInfo)。"""
    if settings.llm_api_key:
        source = settings.llm_key_source
        if not source:
            if (settings.data_dir / LLM_KEY_FILE_NAME).is_file():
                source = "settings_page"
            else:
                source = "env"
        return {
            "configured": True,
            "source": source,
            "masked": mask_llm_key(settings.llm_api_key),
        }
    return {
        "configured": False,
        "source": None,
        "masked": None,
    }


def load_settings(
    data_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    if env is None:
        env = os.environ

    # 1. 确定 data_dir 优先级：参数 > JET_DATA_DIR 环境变量 > DEFAULT_DATA_DIR
    if data_dir is not None:
        resolved_data_dir = Path(data_dir)
    elif "JET_DATA_DIR" in env and env["JET_DATA_DIR"]:
        resolved_data_dir = Path(env["JET_DATA_DIR"])
    else:
        resolved_data_dir = DEFAULT_DATA_DIR

    # 2. 读取 <data_dir>/.env（不创建目录，只读）
    file_env = _parse_env_file(resolved_data_dir / ".env")

    def get_var(key: str, default: str | None = None) -> str | None:
        if key in env:
            return env[key]
        if key in file_env:
            return file_env[key]
        return default

    # 3. 构造 Settings
    port_str = get_var("JET_PORT", "47615")
    port = int(port_str) if port_str is not None else 47615

    llm_base_url = get_var("JET_LLM_BASE_URL", "https://api.deepseek.com") or "https://api.deepseek.com"
    llm_model = get_var("JET_LLM_MODEL", "deepseek-flash") or "deepseek-flash"

    # API Key 优先级：独立文件 llm_api_key > .env / 环境变量 JET_LLM_API_KEY
    file_key = read_llm_key_from_file(resolved_data_dir)
    if file_key:
        llm_api_key = file_key
        llm_key_source = "settings_page"
    else:
        env_llm_key = get_var("JET_LLM_API_KEY")
        if env_llm_key and env_llm_key.strip():
            llm_api_key = env_llm_key.strip()
            llm_key_source = "env"
        else:
            llm_api_key = None
            llm_key_source = None

    daily_limit_str = get_var("JET_DAILY_LLM_LIMIT", "150")
    try:
        daily_llm_limit = int(daily_limit_str) if daily_limit_str is not None else 150
    except ValueError as e:
        raise ValueError(f"JET_DAILY_LLM_LIMIT 必须是 0–500 的整数，当前为：{daily_limit_str}") from e
    if not 0 <= daily_llm_limit <= 500:
        raise ValueError(f"JET_DAILY_LLM_LIMIT 必须是 0–500 的整数，当前为：{daily_llm_limit}")

    daily_assist_limit_str = get_var("JET_DAILY_ASSIST_LIMIT", "50")
    try:
        daily_assist_limit = int(daily_assist_limit_str) if daily_assist_limit_str is not None else 50
    except ValueError as e:
        raise ValueError(f"JET_DAILY_ASSIST_LIMIT 必须是 0–500 的整数，当前为：{daily_assist_limit_str}") from e
    if not 0 <= daily_assist_limit <= 500:
        raise ValueError(f"JET_DAILY_ASSIST_LIMIT 必须是 0–500 的整数，当前为：{daily_assist_limit}")

    judge_concurrency_str = get_var("JET_JUDGE_CONCURRENCY", "3")
    try:
        judge_concurrency = int(judge_concurrency_str) if judge_concurrency_str is not None else 3
    except ValueError as e:
        raise ValueError(f"JET_JUDGE_CONCURRENCY 必须是 1–5 的整数，当前为：{judge_concurrency_str}") from e
    if not 1 <= judge_concurrency <= 5:
        raise ValueError(f"JET_JUDGE_CONCURRENCY 必须是 1–5 的整数，当前为：{judge_concurrency}")

    timeout_str = get_var("JET_LLM_TIMEOUT_S", "20.0")
    llm_timeout_s = float(timeout_str) if timeout_str is not None else 20.0

    p_in_str = get_var("JET_PRICE_INPUT_PER_M", "2.0")
    price_input_per_m = float(p_in_str) if p_in_str is not None else 2.0

    p_cache_str = get_var("JET_PRICE_CACHED_PER_M", "0.04")
    price_cached_per_m = float(p_cache_str) if p_cache_str is not None else 0.04

    p_out_str = get_var("JET_PRICE_OUTPUT_PER_M", "8.0")
    price_output_per_m = float(p_out_str) if p_out_str is not None else 8.0

    prompt_version = get_var("JET_PROMPT_VERSION", DEFAULT_PROMPT_VERSION) or DEFAULT_PROMPT_VERSION
    valid_versions = list_prompt_versions()
    if prompt_version not in valid_versions:
        raise ValueError(
            f"JET_PROMPT_VERSION 必须是可用版本之一（{', '.join(valid_versions)}），当前为：{prompt_version}"
        )
    judge_engine = get_var("JET_JUDGE_ENGINE", "deepseek-flash:no-think") or "deepseek-flash:no-think"

    think_timeout_str = get_var("JET_LLM_THINKING_TIMEOUT_S", "60.0")
    llm_thinking_timeout_s = float(think_timeout_str) if think_timeout_str is not None else 60.0

    eval_calls_str = get_var("JET_EVAL_MAX_CALLS", "300")
    eval_max_calls = int(eval_calls_str) if eval_calls_str is not None else 300

    review_enabled = (get_var("JET_REVIEW_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
    review_engine = get_var("JET_REVIEW_ENGINE", "deepseek-flash:think") or "deepseek-flash:think"

    return Settings(
        data_dir=resolved_data_dir,
        port=port,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_key_source=llm_key_source,
        daily_llm_limit=daily_llm_limit,
        daily_assist_limit=daily_assist_limit,
        judge_concurrency=judge_concurrency,
        llm_timeout_s=llm_timeout_s,
        price_input_per_m=price_input_per_m,
        price_cached_per_m=price_cached_per_m,
        price_output_per_m=price_output_per_m,
        prompt_version=prompt_version,
        judge_engine=judge_engine,
        llm_thinking_timeout_s=llm_thinking_timeout_s,
        eval_max_calls=eval_max_calls,
        review_enabled=review_enabled,
        review_engine=review_engine,
    )

"""
設定ファイルの読み込みとマージ。

優先順位: CLI引数 > job.yaml > config.yaml > デフォルト値
"""

import argparse
import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# プログラム内蔵のデフォルト値
DEFAULTS: dict[str, Any] = {
    "api": {
        "provider": "google",
        "model": "gemini-2.0-flash",
        "api_key_env": "GEMINI_API_KEY",
        "max_retries": 3,
        "max_validation_retries": 2,
        "backoff_base_sec": 2,
        "timeout_sec": 120,
        "rate_limit_respect": True,
        "include_chunk_summary": False,
    },
    "chunking": {
        "target_duration_sec": 300,
        "search_window_stage1_sec": 180,
        "search_window_stage2_sec": 300,
        "gap_threshold_sec": 1.2,
        "overlap_sec": 15,
    },
    "offset": {
        "mode": "auto",
        "manual_offset_sec": 0.0,
        "sample_windows": ["head", "mid", "tail"],
        "window_duration_sec": 300,
        "vtt_search_margin_sec": 300,
        "similarity_threshold": 0.6,
        "min_valid_pairs": 3,
        "mad_k": 3.0,
        "max_offset_sec": 600,
        "use_reading_normalization": False,
    },
    "input": {
        "primary_vtt_filename": "whisper_output.vtt",
        "zoom_vtt_filename": "zoom_output.vtt",
        "dictionary_path": None,
        "speaker_map_path": None,
    },
    "output": {
        "formats": ["txt", "srt", "vtt", "json"],
        "completion_mode": "strict",
        "txt_mode": "clean",
    },
    "logging": {
        "log_level": "INFO",
        "save_prompt": False,
        "save_raw_response": False,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """辞書を再帰的にマージする。overrideの値がbaseを上書きする。

    Args:
        base: ベース辞書
        override: 上書き辞書

    Returns:
        マージ済み辞書
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml(path: Path) -> dict:
    """YAMLファイルを読み込む。存在しない場合は空辞書を返す。

    Args:
        path: YAMLファイルのパス

    Returns:
        パースされた辞書
    """
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def apply_cli_overrides(config: dict, cli_args: argparse.Namespace) -> dict:
    """CLI引数を設定に反映する。

    Args:
        config: マージ済み設定辞書
        cli_args: パース済みCLI引数

    Returns:
        CLI引数反映済みの設定辞書
    """
    result = copy.deepcopy(config)

    # オフセット関連
    if cli_args.offset_sec is not None:
        result["offset"]["mode"] = "manual"
        result["offset"]["manual_offset_sec"] = cli_args.offset_sec
    elif cli_args.offset_skip:
        result["offset"]["mode"] = "skip"

    # 完了モード
    if cli_args.best_effort:
        result["output"]["completion_mode"] = "best_effort"

    # デバッグ
    if cli_args.debug:
        result["logging"]["log_level"] = "DEBUG"

    return result


def resolve_paths(config: dict, job_dir: Path) -> dict:
    """入力ファイルのパスをジョブフォルダ基準で解決する。

    Args:
        config: 設定辞書
        job_dir: ジョブフォルダのパス

    Returns:
        パス解決済みの設定辞書
    """
    result = copy.deepcopy(config)
    input_dir = job_dir / "input"

    # VTTファイルはジョブフォルダの input/ から
    result["_resolved"] = {
        "primary_vtt_path": input_dir / result["input"]["primary_vtt_filename"],
        "zoom_vtt_path": input_dir / result["input"]["zoom_vtt_filename"],
        "job_dir": job_dir,
        "input_dir": input_dir,
        "working_dir": job_dir / "working",
        "temp_dir": job_dir / "working" / "temp",
        "log_dir": job_dir / "working" / "logs",
        "output_dir": job_dir / "output",
    }

    # オプションファイル（辞書・話者マップ）
    for key in ("dictionary_path", "speaker_map_path"):
        path_str = result["input"].get(key)
        if path_str:
            p = Path(path_str)
            if not p.is_absolute():
                p = job_dir / p
            result["_resolved"][key] = p
        else:
            result["_resolved"][key] = None

    return result


def load_config(
    app_config_path: Path,
    job_dir: Path,
    cli_args: argparse.Namespace,
) -> dict:
    """設定を読み込み、4段階でマージして返す。

    優先順位: CLI引数 > job.yaml > config.yaml > デフォルト値

    Args:
        app_config_path: app/config.yaml のパス
        job_dir: ジョブフォルダのパス
        cli_args: パース済みCLI引数

    Returns:
        マージ・解決済みの設定辞書
    """
    # 1. デフォルト値
    config = copy.deepcopy(DEFAULTS)

    # 2. app/config.yaml で上書き
    app_config = load_yaml(app_config_path)
    if app_config:
        config = deep_merge(config, app_config)
        logger.debug(f"app/config.yaml を読み込みました: {app_config_path}")

    # 3. job.yaml で上書き
    job_yaml_path = job_dir / "job.yaml"
    job_config = load_yaml(job_yaml_path)
    if job_config:
        config = deep_merge(config, job_config)
        logger.debug(f"job.yaml を読み込みました: {job_yaml_path}")

    # 4. CLI引数で上書き
    config = apply_cli_overrides(config, cli_args)

    # パス解決
    config = resolve_paths(config, job_dir)

    return config

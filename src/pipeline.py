"""
パイプライン（全体処理フローのオーケストレーション）。

Step 1: データパース
Step 2: 録音オフセット検出・補正
Step 3: ID付与
Step 4: チャンク分割
Step 5: LLM順次処理 + IDバリデーション
Step 6: 最終生成
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.chunker import split_into_chunks
from src.exporter import (
    apply_speaker_map,
    export_json,
    export_offset_report,
    export_srt,
    export_txt,
    export_vtt,
    restore_timestamps,
)
from src.id_manager import IDManager
from src.llm_client import OUTPUT_SCHEMA, build_prompt, get_provider
from src.offset import apply_offset, detect_offset
from src.parser import parse_vtt
from src.resume import ResumeManager
from src.validator import validate_llm_output

logger = logging.getLogger(__name__)


def run_pipeline(config: dict[str, Any], job_dir: Path, clean: bool = False) -> None:
    """メインパイプラインを実行する。

    Args:
        config: マージ済み設定辞書
        job_dir: ジョブフォルダのパス
        clean: Trueの場合、working/temp/ を初期化してから処理を開始する
    """
    resolved = config["_resolved"]
    start_time = time.time()

    # ディレクトリ初期化
    for dir_key in ("working_dir", "temp_dir", "log_dir", "output_dir"):
        resolved[dir_key].mkdir(parents=True, exist_ok=True)

    # ============================================================
    # Step 1: データパース
    # ============================================================
    logger.info("=" * 40 + " Step 1: データパース " + "=" * 40)

    primary_vtt_path = resolved["primary_vtt_path"]
    zoom_vtt_path = resolved["zoom_vtt_path"]

    if not primary_vtt_path.exists():
        raise FileNotFoundError(f"主VTTファイルが見つかりません: {primary_vtt_path}")
    if not zoom_vtt_path.exists():
        raise FileNotFoundError(f"Zoom VTTファイルが見つかりません: {zoom_vtt_path}")

    primary_cues = parse_vtt(primary_vtt_path)
    zoom_cues = parse_vtt(zoom_vtt_path)

    if not primary_cues:
        raise ValueError("主VTTファイルにキューが含まれていません")

    # ============================================================
    # Step 2: 録音オフセット検出・補正
    # ============================================================
    logger.info("=" * 40 + " Step 2: オフセット検出 " + "=" * 40)

    offset_result = detect_offset(primary_cues, zoom_cues, config["offset"])

    # オフセット適用
    zoom_cues, excluded_count = apply_offset(zoom_cues, offset_result.applied_offset_sec)
    offset_result.excluded_vtt_cues = excluded_count

    # オフセットレポート出力
    export_offset_report(offset_result, resolved["output_dir"] / "offset_report.json")

    # ============================================================
    # Step 3: ID付与
    # ============================================================
    logger.info("=" * 40 + " Step 3: ID付与 " + "=" * 40)

    id_manager = IDManager()
    id_cue_pairs = id_manager.assign_ids(primary_cues)

    # ============================================================
    # Step 4: チャンク分割
    # ============================================================
    logger.info("=" * 40 + " Step 4: チャンク分割 " + "=" * 40)

    chunks = split_into_chunks(id_cue_pairs, zoom_cues, config["chunking"])

    if not chunks:
        raise ValueError("チャンク分割の結果が空です")

    # ============================================================
    # Step 5: LLM順次処理
    # ============================================================
    logger.info("=" * 40 + " Step 5: LLM処理 " + "=" * 40)

    resume_mgr = ResumeManager(resolved["temp_dir"])

    # --clean: working/temp/ を初期化してから処理を開始する。
    # 責務: main.py が clean フラグを受け取り、pipeline.py に渡す。
    # 実際の初期化は ResumeManager.clean() が行う。
    if clean:
        resume_mgr.clean()
        logger.info("--clean 指定: working/temp/ を初期化しました")

    # 専門用語辞書の読み込み
    dictionary = None
    dict_path = resolved.get("dictionary_path")
    if dict_path and dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            dictionary = json.load(f)
        logger.info(f"専門用語辞書読み込み: {len(dictionary)}語")

    # LLMプロバイダー初期化
    provider = get_provider(config["api"])
    max_val_retries = config["api"].get("max_validation_retries", 2)

    for chunk in chunks:
        chunk_idx = chunk.index

        # レジューム判定
        if resume_mgr.is_completed(chunk_idx):
            logger.info(f"チャンク{chunk_idx}: 完了済み、スキップ")
            continue

        logger.info(
            f"チャンク{chunk_idx}: 処理開始 "
            f"({chunk.time_range[0]:.1f}–{chunk.time_range[1]:.1f}秒)"
        )

        meta_info = {
            "chunk_index": chunk_idx,
            "chunk_time_range": {
                "start_sec": chunk.time_range[0],
                "end_sec": chunk.time_range[1],
            },
            "input_counts": {
                "primary_cues": len(chunk.srt_cues),
                "zoom_cues": len(chunk.vtt_cues),
            },
            "api_provider": config["api"]["provider"],
            "api_model": config["api"]["model"],
            "offset_applied_sec": offset_result.applied_offset_sec,
        }

        # プロンプト構築
        prompt = build_prompt(chunk, id_cue_pairs, dictionary)

        # プロンプト保存（デバッグ用）
        if config["logging"].get("save_prompt", False):
            prompt_path = resolved["temp_dir"] / f"prompt_chunk_{chunk_idx:03d}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")

        # LLM呼び出し + IDバリデーション
        llm_output = None
        validation_retries = 0

        try:
            while validation_retries <= max_val_retries:
                # API呼び出し（リトライ付き）
                raw_result = provider.call_with_retry(prompt, OUTPUT_SCHEMA)

                # rawレスポンス保存（デバッグ用）
                if config["logging"].get("save_raw_response", False):
                    raw_path = resolved["temp_dir"] / f"raw_chunk_{chunk_idx:03d}.json"
                    with open(raw_path, "w", encoding="utf-8") as f:
                        json.dump(raw_result, f, ensure_ascii=False, indent=2)

                # IDバリデーション
                context_before_ids = [
                    f"U{id_cue_pairs.index((uid, cue)) + 1:06d}"
                    for cue in chunk.context_before
                    for uid, c in id_cue_pairs if c is cue
                ] if chunk.context_before else []
                # 簡易実装: context IDsは本体IDの範囲外として判定
                validation = validate_llm_output(
                    llm_output=raw_result,
                    expected_ids=chunk.srt_ids,
                )

                if validation.passed:
                    llm_output = raw_result
                    meta_info["id_validation"] = {
                        "passed": True,
                        "retries": validation_retries,
                    }
                    break
                else:
                    validation_retries += 1
                    if validation_retries <= max_val_retries:
                        logger.warning(
                            f"チャンク{chunk_idx}: IDバリデーション不合格"
                            f"（試行{validation_retries}/{max_val_retries}）。リトライ"
                        )
                    else:
                        logger.error(
                            f"チャンク{chunk_idx}: IDバリデーション"
                            f"{max_val_retries}回失敗"
                        )
                        meta_info["id_validation"] = {
                            "passed": False,
                            "retries": validation_retries,
                            "errors": validation.errors,
                        }

            if llm_output:
                meta_info["retry_count"] = 0
                resume_mgr.save_result(chunk_idx, llm_output, meta_info)
                logger.info(f"チャンク{chunk_idx}: 完了")
            else:
                raise RuntimeError(
                    f"チャンク{chunk_idx}: LLM出力のバリデーションに失敗"
                )

        except Exception as e:
            logger.error(f"チャンク{chunk_idx}: 処理失敗: {e}")
            resume_mgr.save_error(chunk_idx, e, meta_info)
            # 後続チャンクの処理は継続する

    # ============================================================
    # Step 6: 最終生成
    # ============================================================
    logger.info("=" * 40 + " Step 6: 最終生成 " + "=" * 40)

    # 完了状態の確認
    status = resume_mgr.get_completion_status(len(chunks))
    logger.info(
        f"完了: {len(status['completed'])}, "
        f"失敗: {len(status['failed'])}, "
        f"未処理: {len(status['pending'])}"
    )

    completion_mode = config["output"].get("completion_mode", "strict")
    has_failures = bool(status["failed"] or status["pending"])

    if has_failures and completion_mode == "strict":
        failed_indices = sorted(status["failed"] + status["pending"])
        raise RuntimeError(
            f"欠損チャンクがあるため最終出力を生成できません（strict モード）。"
            f"欠損チャンク: {failed_indices}. "
            f"--best-effort オプションで仮出力を生成できます。"
        )

    # 全チャンクのutterancesを結合
    all_utterances: list[dict] = []
    for chunk in chunks:
        result = resume_mgr.load_result(chunk.index)
        if result and "utterances" in result:
            all_utterances.extend(result["utterances"])
        elif completion_mode == "best_effort":
            # 欠損チャンクのプレースホルダー
            all_utterances.append({
                "id": f"MISSING_CHUNK_{chunk.index}",
                "speaker": "SYSTEM",
                "text": f"（★ チャンク{chunk.index} 処理失敗: "
                        f"{chunk.time_range[0]:.0f}–{chunk.time_range[1]:.0f}秒）",
                "category": "CONTENT",
                "uncertain": True,
                "uncertain_reason": "",
                "uncertain_span_ids": [],
                "source": "PRIMARY",
                "source_ids": chunk.srt_ids,
                "vtt_supplemented": False,
                "edit_type": "UNRESOLVED",
                "edit_note": "チャンク処理失敗",
            })

    # タイムスタンプ復元
    utterances = restore_timestamps(all_utterances, id_manager)

    # 話者マップ適用
    speaker_map_path = resolved.get("speaker_map_path")
    utterances = apply_speaker_map(utterances, speaker_map_path)

    # メタデータ
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_primary_vtt": str(primary_vtt_path.name),
        "source_zoom_vtt": str(zoom_vtt_path.name),
        "applied_offset_sec": offset_result.applied_offset_sec,
        "offset_confidence": offset_result.confidence.value,
        "total_chunks": len(chunks),
        "completed_chunks": len(status["completed"]),
        "total_utterances": len(utterances),
        "llm_provider": config["api"]["provider"],
        "llm_model": config["api"]["model"],
        "completion_mode": completion_mode,
        "processing_time_sec": round(time.time() - start_time, 1),
    }

    # 出力生成
    output_dir = resolved["output_dir"]
    formats = config["output"].get("formats", ["txt", "srt", "vtt", "json"])

    if "txt" in formats:
        export_txt(utterances, output_dir / "final_transcript.txt", id_manager)
    if "srt" in formats:
        export_srt(utterances, output_dir / "final_transcript.srt")
    if "vtt" in formats:
        export_vtt(utterances, output_dir / "final_transcript.vtt")
    if "json" in formats:
        export_json(utterances, output_dir / "final_transcript.json", metadata)

    logger.info(f"最終出力完了: {output_dir}")
    logger.info(f"総処理時間: {time.time() - start_time:.1f}秒")

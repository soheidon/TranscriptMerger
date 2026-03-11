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
from src.offset import apply_offset, build_no_secondary_offset_result, detect_offset
from src.parser import parse_vtt
from src.resume import ResumeManager
from src.validator import validate_llm_output

logger = logging.getLogger(__name__)


def run_pipeline(
    config: dict[str, Any],
    job_dir: Path,
    clean: bool = False,
    selected_chunks: set[int] | None = None,
) -> None:
    """メインパイプラインを実行する。

    Args:
        config: マージ済み設定辞書
        job_dir: ジョブフォルダのパス
        clean: Trueの場合、working/temp/ を初期化してから処理を開始する
        selected_chunks: 指定時は該当チャンクのみ再処理する
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
    use_secondary_vtt = resolved.get("use_secondary_vtt", True)

    if not primary_vtt_path.exists():
        raise FileNotFoundError(f"主VTTファイルが見つかりません: {primary_vtt_path}")

    primary_cues = parse_vtt(primary_vtt_path)

    if use_secondary_vtt:
        if not zoom_vtt_path.exists():
            raise FileNotFoundError(f"Zoom VTTファイルが見つかりません: {zoom_vtt_path}")
        zoom_cues = parse_vtt(zoom_vtt_path)
    else:
        logger.info("単一VTTモード: 補助VTTなしで処理します")
        zoom_cues = []

    if not primary_cues:
        raise ValueError("主VTTファイルにキューが含まれていません")

    logger.info(f"主VTTキュー数: {len(primary_cues)}")
    logger.info(f"Zoom VTTキュー数: {len(zoom_cues)}")

    # ============================================================
    # Step 2: 録音オフセット検出・補正
    # ============================================================
    logger.info("=" * 40 + " Step 2: オフセット検出 " + "=" * 40)

    if use_secondary_vtt:
        offset_result = detect_offset(primary_cues, zoom_cues, config["offset"])
        zoom_cues, excluded_count = apply_offset(zoom_cues, offset_result.applied_offset_sec)
        offset_result.excluded_vtt_cues = excluded_count
    else:
        offset_result = build_no_secondary_offset_result(method="single_vtt")

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

    # 部分再処理の対象抽出
    if selected_chunks is not None:
        target_chunks = [c for c in chunks if c.index in selected_chunks]
        missing = sorted(selected_chunks - {c.index for c in chunks})
        if missing:
            raise ValueError(f"指定されたチャンク番号が存在しません: {missing}")
        logger.info(
            "部分再処理モード: 対象チャンク=%s",
            [c.index for c in target_chunks],
        )
    else:
        target_chunks = chunks

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

    # 専門用語辞書 / glossary の読み込み
    dictionary = None
    input_dir = resolved.get("input_dir")

    # 1. glossary_confirmed.tsv を最優先で見る
    glossary_terms: list[str] = []
    if input_dir:
        glossary_tsv = input_dir / "glossary_confirmed.tsv"
        glossary_txt = input_dir / "glossary.txt"

        if glossary_tsv.exists():
            with open(glossary_tsv, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    term = parts[0].strip()
                    # ヘッダ行（表記 等）はスキップ
                    if not term or term in ("表記", "用語"):
                        continue
                    glossary_terms.append(term)
            if glossary_terms:
                dictionary = {"用語": glossary_terms}
                logger.info(f"glossary_confirmed.tsv から {len(glossary_terms)} 語を読み込みました")
        else:
            # glossary.txt はあるが confirmed.tsv がない場合は警告のみ
            if glossary_txt.exists():
                logger.warning(
                    "glossary.txt は存在しますが glossary_confirmed.tsv が見つかりません。"
                    "glossary 前処理を実行するか、glossary_confirmed.tsv を作成してください。"
                )

    # 2. 従来の JSON 辞書（dictionary_path）があればフォールバックとして使用
    if dictionary is None:
        dict_path = resolved.get("dictionary_path")
        if dict_path and dict_path.exists():
            with open(dict_path, "r", encoding="utf-8") as f:
                dictionary = json.load(f)
            logger.info(f"専門用語辞書読み込み: type={type(dictionary).__name__}")

    # コンテキストプロンプトの読み込み（任意）
    context_prompt = None
    if input_dir:
        for ctx_filename in ("context_prompt.txt", "context_prompt.md"):
            ctx_path = input_dir / ctx_filename
            if ctx_path.exists():
                context_prompt = ctx_path.read_text(encoding="utf-8").strip()
                logger.info(f"コンテキストプロンプト読み込み: {ctx_path}")
                break

    # LLMプロバイダー初期化
    provider = get_provider(config["api"])
    max_val_retries = config["api"].get("max_validation_retries", 2)

    total_target_chunks = len(target_chunks)
    for i, chunk in enumerate(target_chunks):
        chunk_idx = chunk.index

        # レジューム判定
        if resume_mgr.is_completed(chunk_idx):
            logger.info(
                f"[{i+1}/{total_target_chunks}] チャンク{chunk_idx}: 完了済み、スキップ"
            )
            continue

        logger.info(
            f"[{i+1}/{total_target_chunks}] チャンク{chunk_idx}: 処理中 "
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

        # プロンプト構築（リトライ時は前回のバリデーション誤りを渡す）
        validation_feedback: list[str] | None = None
        prompt = build_prompt(
            chunk,
            id_cue_pairs,
            dictionary,
            context_prompt,
            validation_feedback,
            use_secondary_vtt=use_secondary_vtt,
        )

        # LLM呼び出し + IDバリデーション
        llm_output = None
        validation_retries = 0

        try:
            while validation_retries <= max_val_retries:
                # プロンプト保存（デバッグ用）
                if config["logging"].get("save_prompt", False):
                    prompt_path = resolved["temp_dir"] / f"prompt_chunk_{chunk_idx:03d}.txt"
                    prompt_path.write_text(prompt, encoding="utf-8")

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
                        validation_feedback = validation.errors
                        prompt = build_prompt(
                            chunk,
                            id_cue_pairs,
                            dictionary,
                            context_prompt,
                            validation_feedback,
                            use_secondary_vtt=use_secondary_vtt,
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
                # チャンク品質統計
                utts = llm_output.get("utterances", [])
                n_uncertain = sum(1 for u in utts if u.get("uncertain"))
                n_ab = sum(1 for u in utts if str(u.get("uncertain_reason", "")) == "AB_MISMATCH")
                n_bc = sum(1 for u in utts if str(u.get("category", "")) == "BACKCHANNEL")
                logger.info(
                    f"[{i+1}/{total_target_chunks}] チャンク{chunk_idx}: 完了 "
                    f"(utterances={len(utts)}, uncertain={n_uncertain}, AB_MISMATCH={n_ab}, BACKCHANNEL={n_bc})"
                )
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
                "uncertain_reason": "NONE",
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
        "source_zoom_vtt": str(zoom_vtt_path.name) if use_secondary_vtt else None,
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

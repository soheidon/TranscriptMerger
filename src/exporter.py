"""
最終出力生成。

全チャンクのLLM出力を結合し、ID対応表からタイムスタンプを復元して、
TXT / SRT / VTT / JSON を出力する。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.id_manager import IDManager
from src.models import Category, Utterance

logger = logging.getLogger(__name__)


def _format_timestamp_srt(seconds: float) -> str:
    """秒数をSRT形式のタイムスタンプに変換する。"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_timestamp_vtt(seconds: float) -> str:
    """秒数をVTT形式のタイムスタンプに変換する。"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def restore_timestamps(
    all_utterances: list[dict],
    id_manager: IDManager,
) -> list[Utterance]:
    """LLM出力のutteranceにタイムスタンプを復元する。

    Args:
        all_utterances: 全チャンクのutteranceを結合したリスト
        id_manager: ID管理オブジェクト

    Returns:
        タイムスタンプ復元済みのUtteranceリスト
    """
    results: list[Utterance] = []

    for utt_dict in all_utterances:
        source_ids = utt_dict.get("source_ids", [])
        time_range = id_manager.get_time_range(source_ids)

        start = time_range[0] if time_range else None
        end = time_range[1] if time_range else None

        # V_INSERT_* の場合
        if not time_range and utt_dict.get("id", "").startswith("V_INSERT_"):
            vtt_entry = id_manager.get_vtt_insert(utt_dict["id"])
            if vtt_entry:
                start = vtt_entry["start"]
                end = vtt_entry["end"]

        utt = Utterance(
            id=utt_dict.get("id", ""),
            speaker=utt_dict.get("speaker", "UNKNOWN"),
            text=utt_dict.get("text", ""),
            category=Category(utt_dict.get("category", "CONTENT")),
            uncertain=utt_dict.get("uncertain", False),
            uncertain_reason=utt_dict.get("uncertain_reason", "NONE"),
            uncertain_span_ids=utt_dict.get("uncertain_span_ids", []),
            source=utt_dict.get("source", "PRIMARY"),
            source_ids=source_ids,
            vtt_supplemented=utt_dict.get("vtt_supplemented", False),
            edit_type=utt_dict.get("edit_type", "NONE"),
            edit_note=utt_dict.get("edit_note", ""),
            start=start,
            end=end,
        )
        results.append(utt)

    logger.info(f"タイムスタンプ復元完了: {len(results)}発話")
    return results


def _should_exclude_from_txt(utt: Utterance) -> bool:
    """TXT出力からこの発話を除外すべきか判定する。

    BACKCHANNELのうち短い相槌、uncertain+BACKCHANNEL、
    不確実な短いノイズパターンを除外する。
    """
    import re

    # 既存のBACKCHANNEL除外ロジック（短い相槌）
    if utt.category == Category.BACKCHANNEL:
        if len(utt.text) <= 10:
            if not re.search(r"\d", utt.text):
                keep_words = [
                    "いいえ", "違う", "だめ", "ない", "ません",
                    "お願い", "ください", "了解", "承知",
                ]
                if not any(w in utt.text for w in keep_words):
                    return True

    # 追加1：uncertain + BACKCHANNEL は常にTXTから除外
    if utt.uncertain and utt.category == Category.BACKCHANNEL:
        return True

    # 追加2：不確実な短いノイズパターンの除去
    if utt.uncertain and len(utt.text) <= 15:
        noise_patterns = [
            "ごめん", "ごめんごめん",
            "そう", "でも",
            "ありがとうございました",
            "よいしょ", "いいっしょ",
            "ごちそう",
            "おはようございます",
        ]
        if utt.text.rstrip("。、！？ ") in noise_patterns:
            return True

    return False


def apply_speaker_map(
    utterances: list[Utterance],
    speaker_map_path: Path | None,
) -> list[Utterance]:
    """話者マップを適用する。

    Args:
        utterances: Utteranceリスト
        speaker_map_path: speaker_map.json のパス（Noneなら何もしない）

    Returns:
        話者名置換済みのUtteranceリスト
    """
    if not speaker_map_path or not speaker_map_path.exists():
        return utterances

    with open(speaker_map_path, "r", encoding="utf-8") as f:
        speaker_map = json.load(f)

    for utt in utterances:
        if utt.speaker in speaker_map:
            utt.speaker = speaker_map[utt.speaker]

    logger.info(f"話者マップ適用: {speaker_map}")
    return utterances


def export_txt(
    utterances: list[Utterance],
    output_path: Path,
    id_manager: IDManager,
) -> None:
    """TXT（読み物版）を出力する。"""
    lines = []
    current_speaker = None

    for utt in utterances:
        if _should_exclude_from_txt(utt):
            continue

        # 話者が変わったらラベルを出力
        if utt.speaker != current_speaker:
            if current_speaker is not None:
                lines.append("")
            lines.append(utt.speaker)
            current_speaker = utt.speaker

        # テキスト本体 + 不確実注記（あれば末尾に付与）
        # 話者曖昧 (SPEAKER_AMBIGUOUS) の場合はテキスト自体は明瞭なためタグを付けない。
        # AB_MISMATCH でも、NORMALIZE/VTT_SUPPLEMENT で整形済みならTXTではタグを外す。
        show_uncertain_tag = (
            utt.uncertain
            and str(utt.uncertain_reason) != "SPEAKER_AMBIGUOUS"
            and not (
                str(utt.uncertain_reason) == "AB_MISMATCH"
                and str(utt.edit_type) in ("NORMALIZE", "VTT_SUPPLEMENT")
            )
        )

        if show_uncertain_tag:
            if utt.start is not None:
                minutes = int(utt.start // 60)
                seconds = int(utt.start % 60)
                ts_hint = f"{minutes:02d}:{seconds:02d}頃"
                lines.append(f"{utt.text} [聞き取り不確実 {ts_hint}]")
            else:
                lines.append(f"{utt.text} [聞き取り不確実]")
        else:
            lines.append(utt.text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"TXT出力: {output_path}")


def export_srt(utterances: list[Utterance], output_path: Path) -> None:
    """SRT（レビュー・編集用）を出力する。"""
    lines = []
    for i, utt in enumerate(utterances, start=1):
        if utt.start is None or utt.end is None:
            continue
        lines.append(str(i))
        lines.append(
            f"{_format_timestamp_srt(utt.start)} --> {_format_timestamp_srt(utt.end)}"
        )
        lines.append(f"{utt.speaker}: {utt.text}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"SRT形式出力: {output_path}")


def export_vtt(utterances: list[Utterance], output_path: Path) -> None:
    """VTT（Web互換）を出力する。"""
    lines = ["WEBVTT", ""]
    for utt in utterances:
        if utt.start is None or utt.end is None:
            continue
        lines.append(
            f"{_format_timestamp_vtt(utt.start)} --> {_format_timestamp_vtt(utt.end)}"
        )
        lines.append(f"<v {utt.speaker}>{utt.text}</v>")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"VTT出力: {output_path}")


def export_json(
    utterances: list[Utterance],
    output_path: Path,
    metadata: dict[str, Any],
) -> None:
    """JSON（正本）を出力する。"""
    data = {
        "metadata": metadata,
        "utterances": [
            {
                "id": utt.id,
                "start": utt.start,
                "end": utt.end,
                "speaker": utt.speaker,
                "text": utt.text,
                "category": utt.category.value,
                "uncertain": utt.uncertain,
                "uncertain_reason": str(utt.uncertain_reason) if utt.uncertain_reason else "NONE",
                "source": utt.source,
                "source_ids": utt.source_ids,
                "vtt_supplemented": utt.vtt_supplemented,
                "edit_type": utt.edit_type if isinstance(utt.edit_type, str) else utt.edit_type.value,
                "edit_note": utt.edit_note,
            }
            for utt in utterances
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON出力: {output_path}")


def export_offset_report(
    offset_result: Any,
    output_path: Path,
) -> None:
    """オフセットレポートを出力する。"""
    from dataclasses import asdict
    data = asdict(offset_result)
    # OffsetConfidence enumをstr化
    if "confidence" in data:
        data["confidence"] = str(data["confidence"].value) if hasattr(data["confidence"], "value") else str(data["confidence"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"オフセットレポート出力: {output_path}")

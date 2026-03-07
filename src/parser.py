"""
VTTファイルのパーサー（主VTT・Zoom VTT共通）。parse_srtはSRT入力のレガシー対応用。

主VTT（Whisper+pyannote）とZoom VTTを読み込み、
統一的な Cue オブジェクトのリストに変換する。
"""

import logging
import re
from pathlib import Path

from src.models import Cue

logger = logging.getLogger(__name__)


def parse_timestamp_srt(ts: str) -> float:
    """SRT形式のタイムスタンプを秒に変換する。

    Args:
        ts: "HH:MM:SS,mmm" 形式の文字列

    Returns:
        秒数（float）
    """
    # SRT形式はカンマ区切り: 00:01:23,456
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def parse_timestamp_vtt(ts: str) -> float:
    """VTT形式のタイムスタンプを秒に変換する。

    Args:
        ts: "HH:MM:SS.mmm" または "MM:SS.mmm" 形式の文字列

    Returns:
        秒数（float）
    """
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    elif len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        seconds = float(parts[1])
    else:
        raise ValueError(f"不正なVTTタイムスタンプ: {ts}")
    return hours * 3600 + minutes * 60 + seconds


def extract_speaker_from_text(text: str) -> tuple[str | None, str]:
    """テキストから話者ラベルを抽出する。

    pyannote形式（"SPEAKER_00: テキスト"）を想定。

    Args:
        text: キューのテキスト

    Returns:
        (話者ラベル or None, 話者ラベルを除いたテキスト)
    """
    # パターン: "SPEAKER_XX: " または "SPEAKER_XX " で始まる
    match = re.match(r"^(SPEAKER_\d+)\s*[:：]\s*", text)
    if match:
        speaker = match.group(1)
        remaining = text[match.end():]
        return speaker, remaining
    return None, text


def parse_srt(file_path: Path) -> list[Cue]:
    """SRTファイルをパースする（レガシー対応用）。

    Args:
        file_path: SRT形式ファイルのパス

    Returns:
        Cueオブジェクトのリスト
    """
    logger.info(f"SRT形式ファイルを読み込み中: {file_path}")

    text = file_path.read_text(encoding="utf-8-sig")  # BOM対応
    cues: list[Cue] = []

    # SRT形式ブロック: 連番 / タイムコード / テキスト（空行区切り）
    blocks = re.split(r"\n\s*\n", text.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # 1行目: 連番
        try:
            index = int(lines[0].strip())
        except ValueError:
            logger.warning(f"SRT形式連番のパースに失敗: {lines[0]}")
            continue

        # 2行目: タイムコード
        tc_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1].strip(),
        )
        if not tc_match:
            logger.warning(f"SRT形式タイムコードのパースに失敗: {lines[1]}")
            continue

        start = parse_timestamp_srt(tc_match.group(1))
        end = parse_timestamp_srt(tc_match.group(2))

        # 3行目以降: テキスト
        raw_text = "\n".join(lines[2:]).strip()
        speaker, clean_text = extract_speaker_from_text(raw_text)

        cues.append(Cue(
            index=index,
            start=start,
            end=end,
            speaker=speaker,
            text=clean_text,
        ))

    logger.info(f"SRT形式: {len(cues)} キューを読み込みました")
    return cues


def parse_vtt(file_path: Path) -> list[Cue]:
    """VTTファイルをパースする。

    Args:
        file_path: VTTファイルのパス

    Returns:
        Cueオブジェクトのリスト
    """
    logger.info(f"VTTファイルを読み込み中: {file_path}")

    text = file_path.read_text(encoding="utf-8-sig")  # BOM対応
    cues: list[Cue] = []

    # WEBVTTヘッダーをスキップ
    lines = text.strip().split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("WEBVTT"):
            start_idx = i + 1
            break

    # 空行を飛ばしてブロック開始を見つける
    content = "\n".join(lines[start_idx:])
    blocks = re.split(r"\n\s*\n", content.strip())

    cue_index = 0
    for block in blocks:
        block_lines = block.strip().split("\n")
        if not block_lines:
            continue

        # タイムコード行を探す
        tc_line_idx = None
        for i, line in enumerate(block_lines):
            if "-->" in line:
                tc_line_idx = i
                break

        if tc_line_idx is None:
            continue

        # タイムコード
        tc_match = re.match(
            r"(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
            r"\s*-->\s*"
            r"(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})",
            block_lines[tc_line_idx].strip(),
        )
        if not tc_match:
            logger.warning(f"VTTタイムコードのパースに失敗: {block_lines[tc_line_idx]}")
            continue

        start = parse_timestamp_vtt(tc_match.group(1))
        end = parse_timestamp_vtt(tc_match.group(2))

        # テキスト（タイムコード行の後ろ）
        text_lines = block_lines[tc_line_idx + 1:]
        raw_text = "\n".join(text_lines).strip()

        # VTT voice tag から話者を抽出: <v SPEAKER_00>テキスト</v>
        speaker = None
        voice_match = re.match(r"<v\s+([^>]+)>(.*?)(?:</v>)?$", raw_text, re.DOTALL)
        if voice_match:
            speaker = voice_match.group(1).strip()
            raw_text = voice_match.group(2).strip()

        cue_index += 1
        cues.append(Cue(
            index=cue_index,
            start=start,
            end=end,
            speaker=speaker,
            text=raw_text,
        ))

    logger.info(f"VTT: {len(cues)} キューを読み込みました")
    return cues

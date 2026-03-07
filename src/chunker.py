"""
チャンク分割（同期スプリット）。

主VTTタイムスタンプ基準で5分間隔に分割し、
各チャンクに前後オーバーラップ区間を付与する。
VTT側は最近傍タイムスタンプで同期して切り分ける。
"""

import logging
from typing import Any

from src.models import Chunk, Cue

logger = logging.getLogger(__name__)


def _find_split_point(
    id_cue_pairs: list[tuple[str, Cue]],
    target_sec: float,
    search_window_sec: float,
    gap_threshold_sec: float,
) -> int | None:
    """分割点のインデックスを探索する。

    優先順位:
    1. 字幕ギャップ（次の開始 - 前の終了 >= gap_threshold）
    2. 話者切替直後
    3. 文末（句点等）
    4. 最も近いキュー境界（強制分割）

    Args:
        id_cue_pairs: (ID, Cue) のリスト
        target_sec: 分割ターゲット時刻（秒）
        search_window_sec: 探索窓の半幅（秒）
        gap_threshold_sec: ギャップ閾値（秒）

    Returns:
        分割点のインデックス（この位置の直前で分割）。見つからない場合はNone
    """
    if len(id_cue_pairs) < 2:
        return None

    # 探索範囲内の候補を収集
    candidates_gap = []
    candidates_speaker = []
    candidates_sentence = []
    candidates_nearest = []

    for i in range(1, len(id_cue_pairs)):
        cue_time = id_cue_pairs[i][1].start
        if abs(cue_time - target_sec) > search_window_sec:
            continue

        distance = abs(cue_time - target_sec)
        prev_cue = id_cue_pairs[i - 1][1]
        curr_cue = id_cue_pairs[i][1]

        # 1. ギャップ
        gap = curr_cue.start - prev_cue.end
        if gap >= gap_threshold_sec:
            candidates_gap.append((i, distance))

        # 2. 話者切替
        if prev_cue.speaker and curr_cue.speaker and prev_cue.speaker != curr_cue.speaker:
            candidates_speaker.append((i, distance))

        # 3. 文末
        if prev_cue.text.rstrip().endswith(("。", ".", "！", "！", "？", "?")):
            candidates_sentence.append((i, distance))

        # 4. 最近傍（全候補）
        candidates_nearest.append((i, distance))

    # 優先度順に、最もターゲットに近いものを返す
    for candidates in [candidates_gap, candidates_speaker, candidates_sentence, candidates_nearest]:
        if candidates:
            candidates.sort(key=lambda x: x[1])
            return candidates[0][0]

    return None


def _find_nearest_vtt_index(
    vtt_cues: list[Cue], target_sec: float
) -> int:
    """VTT側で target_sec に最も近いキューのインデックスを返す。"""
    if not vtt_cues:
        return 0
    best_idx = 0
    best_dist = abs(vtt_cues[0].start - target_sec)
    for i, cue in enumerate(vtt_cues):
        dist = abs(cue.start - target_sec)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def split_into_chunks(
    id_cue_pairs: list[tuple[str, Cue]],
    vtt_cues: list[Cue],
    config: dict[str, Any],
) -> list[Chunk]:
    """主VTTとZoom VTTをチャンクに分割する。

    Args:
        id_cue_pairs: (ID, Cue) のリスト（主VTT側、ID付与済み）
        vtt_cues: VTTキューのリスト（オフセット補正済み）
        config: chunking設定辞書

    Returns:
        Chunkオブジェクトのリスト
    """
    target_dur = config.get("target_duration_sec", 300)
    window1 = config.get("search_window_stage1_sec", 180)
    window2 = config.get("search_window_stage2_sec", 300)
    gap_thresh = config.get("gap_threshold_sec", 1.2)
    overlap_sec = config.get("overlap_sec", 15)

    if not id_cue_pairs:
        logger.warning("主VTTキューが空です")
        return []

    total_duration = id_cue_pairs[-1][1].end
    logger.info(f"総時間: {total_duration:.1f}秒, 目標チャンク長: {target_dur}秒")

    # 分割点を探索
    split_indices = [0]  # 最初のチャンクは先頭から
    target_sec = target_dur

    while target_sec < total_duration:
        # 第1段階: ±window1
        split_idx = _find_split_point(id_cue_pairs, target_sec, window1, gap_thresh)
        if split_idx is None:
            # 第2段階: ±window2
            split_idx = _find_split_point(id_cue_pairs, target_sec, window2, gap_thresh)
        if split_idx is None:
            # それでも見つからない場合はスキップ（残りを最後のチャンクに含める）
            logger.warning(f"分割点が見つかりません（target={target_sec:.0f}秒）")
            break

        if split_idx not in split_indices:
            split_indices.append(split_idx)

        actual_time = id_cue_pairs[split_idx][1].start
        target_sec = actual_time + target_dur

    # 末尾を追加
    split_indices.append(len(id_cue_pairs))

    # チャンクを構築
    chunks: list[Chunk] = []

    for chunk_idx in range(len(split_indices) - 1):
        start_i = split_indices[chunk_idx]
        end_i = split_indices[chunk_idx + 1]

        # 本体
        body_pairs = id_cue_pairs[start_i:end_i]
        body_ids = [uid for uid, _ in body_pairs]
        body_cues = [cue for _, cue in body_pairs]
        time_start = body_cues[0].start
        time_end = body_cues[-1].end

        # 前オーバーラップ
        context_before = []
        if chunk_idx > 0:
            overlap_start = max(0.0, time_start - overlap_sec)
            context_before = [
                cue for _, cue in id_cue_pairs[:start_i]
                if cue.start >= overlap_start
            ]

        # 後オーバーラップ
        context_after = []
        if chunk_idx < len(split_indices) - 2:
            overlap_end = time_end + overlap_sec
            context_after = [
                cue for _, cue in id_cue_pairs[end_i:]
                if cue.end <= overlap_end
            ]

        # VTT側の同期
        vtt_start_idx = _find_nearest_vtt_index(vtt_cues, time_start)
        vtt_end_idx = _find_nearest_vtt_index(vtt_cues, time_end)
        chunk_vtt = vtt_cues[vtt_start_idx:vtt_end_idx + 1]

        chunk = Chunk(
            index=chunk_idx,
            srt_cues=body_cues,
            vtt_cues=chunk_vtt,
            srt_ids=body_ids,
            time_range=(time_start, time_end),
            context_before=context_before,
            context_after=context_after,
        )
        chunks.append(chunk)

        logger.info(
            f"チャンク{chunk_idx}: {time_start:.1f}–{time_end:.1f}秒, "
            f"主VTT={len(body_cues)}, Zoom={len(chunk_vtt)}, "
            f"ctx_before={len(context_before)}, ctx_after={len(context_after)}"
        )

    logger.info(f"チャンク分割完了: {len(chunks)}チャンク")
    return chunks

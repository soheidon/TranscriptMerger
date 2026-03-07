"""
録音オフセット検出・補正。

主VTTとZoom VTTの録音開始時刻のズレを、複数窓テキストマッチングで自動推定する。
外れ値除去にはMAD（Median Absolute Deviation）を使用する。
"""

import logging
from difflib import SequenceMatcher
from statistics import median, stdev
from typing import Any

from src.models import Cue, OffsetConfidence, OffsetResult
from src.normalizer import (
    char_ngrams,
    jaccard_similarity,
    normalize_for_matching,
)

logger = logging.getLogger(__name__)


def _compute_similarity(text_a: str, text_b: str) -> float:
    """ハイブリッドテキスト類似度を計算する。

    3-gram Jaccard (0.5) + SequenceMatcher (0.3) + キーワード一致 (0.2)

    Args:
        text_a: 正規化済みテキストA
        text_b: 正規化済みテキストB

    Returns:
        類似度スコア（0.0〜1.0）
    """
    if not text_a or not text_b:
        return 0.0

    # 3-gram Jaccard
    ngrams_a = char_ngrams(text_a, n=3)
    ngrams_b = char_ngrams(text_b, n=3)
    jaccard = jaccard_similarity(ngrams_a, ngrams_b)

    # SequenceMatcher
    seq_ratio = SequenceMatcher(None, text_a, text_b).ratio()

    # キーワード一致（数字の一致をチェック）
    import re
    nums_a = set(re.findall(r"\d+", text_a))
    nums_b = set(re.findall(r"\d+", text_b))
    if nums_a or nums_b:
        keyword_match = len(nums_a & nums_b) / max(len(nums_a | nums_b), 1)
    else:
        keyword_match = 0.5  # 数字がない場合はニュートラル

    return 0.5 * jaccard + 0.3 * seq_ratio + 0.2 * keyword_match


def _get_sample_windows(
    srt_cues: list[Cue],
    window_names: list[str],
    window_duration_sec: float,
) -> list[tuple[str, float, float]]:
    """サンプル窓の時間範囲を決定する。

    Args:
        srt_cues: 主VTTキューのリスト
        window_names: 窓の種別リスト（["head", "mid", "tail"]）
        window_duration_sec: 各窓の長さ（秒）

    Returns:
        (窓名, 開始秒, 終了秒) のリスト
    """
    if not srt_cues:
        return []

    total_duration = srt_cues[-1].end
    windows = []

    for name in window_names:
        if name == "head":
            start = 0.0
            end = min(window_duration_sec, total_duration)
        elif name == "mid":
            mid_point = total_duration * 0.45
            start = max(0.0, mid_point - window_duration_sec / 2)
            end = min(total_duration, mid_point + window_duration_sec / 2)
        elif name == "tail":
            end = total_duration
            start = max(0.0, end - window_duration_sec)
        else:
            logger.warning(f"不明な窓名: {name}")
            continue

        # 短い録音では窓が重複する場合があるのでスキップ
        if total_duration < window_duration_sec * 1.5 and name != "head":
            logger.info(f"録音が短いため {name} 窓をスキップ")
            continue

        windows.append((name, start, end))

    return windows


def _filter_cues_by_range(
    cues: list[Cue], start: float, end: float
) -> list[Cue]:
    """時間範囲内のキューを抽出する。"""
    return [c for c in cues if c.start >= start and c.end <= end]


def _compute_mad(values: list[float]) -> float:
    """MAD（Median Absolute Deviation）を計算する。

    Args:
        values: 数値のリスト

    Returns:
        MAD値
    """
    if not values:
        return 0.0
    med = median(values)
    deviations = [abs(v - med) for v in values]
    return median(deviations)


def detect_offset(
    srt_cues: list[Cue],
    vtt_cues: list[Cue],
    config: dict[str, Any],
) -> OffsetResult:
    """主VTTとZoom VTTの録音オフセットを検出する。

    Args:
        srt_cues: 主VTTキューのリスト
        vtt_cues: Zoom VTTキューのリスト
        config: offset設定辞書

    Returns:
        OffsetResult
    """
    mode = config.get("mode", "auto")

    # skip モード
    if mode == "skip":
        logger.info("オフセット検出をスキップ（offset=0）")
        return OffsetResult(
            estimated_offset_sec=0.0,
            confidence=OffsetConfidence.HIGH,
            valid_pairs=0,
            total_pairs_before_filter=0,
            mad=0.0,
            std_dev=0.0,
            method="skip",
            sample_windows_used=[],
            drift_detected=False,
            drift_delta_sec=None,
            top_candidates=[],
            applied_offset_sec=0.0,
            override=None,
            excluded_vtt_cues=0,
        )

    # manual モード
    if mode == "manual":
        manual_sec = config.get("manual_offset_sec", 0.0)
        logger.info(f"手動オフセット指定: {manual_sec}秒")
        return OffsetResult(
            estimated_offset_sec=manual_sec,
            confidence=OffsetConfidence.HIGH,
            valid_pairs=0,
            total_pairs_before_filter=0,
            mad=0.0,
            std_dev=0.0,
            method="manual",
            sample_windows_used=[],
            drift_detected=False,
            drift_delta_sec=None,
            top_candidates=[],
            applied_offset_sec=manual_sec,
            override=manual_sec,
            excluded_vtt_cues=0,
        )

    # auto モード
    logger.info("オフセット自動検出を開始")

    use_reading = config.get("use_reading_normalization", False)
    similarity_threshold = config.get("similarity_threshold", 0.6)
    max_offset = config.get("max_offset_sec", 600)
    min_valid = config.get("min_valid_pairs", 3)
    mad_k = config.get("mad_k", 3.0)
    window_duration = config.get("window_duration_sec", 300)
    window_names = config.get("sample_windows", ["head", "mid", "tail"])
    vtt_margin = config.get("vtt_search_margin_sec", 300)

    # サンプル窓の決定
    windows = _get_sample_windows(srt_cues, window_names, window_duration)
    logger.info(f"サンプル窓: {[(n, f'{s:.0f}-{e:.0f}s') for n, s, e in windows]}")

    # 各窓でペアマッチング
    all_deltas: list[float] = []
    window_deltas: dict[str, list[float]] = {}

    for win_name, win_start, win_end in windows:
        srt_window = _filter_cues_by_range(srt_cues, win_start, win_end)
        # VTT側は広めに取る（オフセット分を考慮）
        vtt_search_start = max(0, win_start - max_offset - vtt_margin)
        vtt_search_end = win_end + max_offset + vtt_margin
        vtt_window = _filter_cues_by_range(vtt_cues, vtt_search_start, vtt_search_end)

        # 主VTTの正規化テキストを事前計算
        srt_normalized = [
            (cue, normalize_for_matching(cue.text, use_reading=use_reading))
            for cue in srt_window
        ]
        vtt_normalized = [
            (cue, normalize_for_matching(cue.text, use_reading=use_reading))
            for cue in vtt_window
        ]

        deltas = []
        for srt_cue, srt_text in srt_normalized:
            if not srt_text:
                continue
            best_sim = 0.0
            best_delta = None
            for vtt_cue, vtt_text in vtt_normalized:
                if not vtt_text:
                    continue
                sim = _compute_similarity(srt_text, vtt_text)
                if sim > best_sim:
                    best_sim = sim
                    best_delta = vtt_cue.start - srt_cue.start

            if best_sim >= similarity_threshold and best_delta is not None:
                if abs(best_delta) <= max_offset:
                    deltas.append(best_delta)

        window_deltas[win_name] = deltas
        all_deltas.extend(deltas)
        logger.info(f"  {win_name}: {len(deltas)} ペアマッチ")

    total_pairs = len(all_deltas)
    logger.info(f"総ペア数: {total_pairs}")

    # ペアが不足
    if total_pairs == 0:
        logger.warning("有効ペアが0件。offset=0にフォールバック")
        return OffsetResult(
            estimated_offset_sec=0.0,
            confidence=OffsetConfidence.LOW,
            valid_pairs=0,
            total_pairs_before_filter=0,
            mad=0.0,
            std_dev=0.0,
            method="auto",
            sample_windows_used=[w[0] for w in windows],
            drift_detected=False,
            drift_delta_sec=None,
            top_candidates=[],
            applied_offset_sec=0.0,
            override=None,
            excluded_vtt_cues=0,
        )

    # Phase 2: MADベース外れ値除去
    med = median(all_deltas)
    mad_val = _compute_mad(all_deltas)

    if mad_val > 0:
        filtered = [d for d in all_deltas if abs(d - med) <= mad_k * mad_val]
    else:
        filtered = all_deltas[:]

    if not filtered:
        filtered = all_deltas[:]

    final_offset = median(filtered)
    final_std = stdev(filtered) if len(filtered) >= 2 else 0.0

    # Phase 3: 信頼性判定
    valid_count = len(filtered)
    if valid_count >= 5 and final_std <= 1.0:
        confidence = OffsetConfidence.HIGH
    elif valid_count >= min_valid and final_std <= 2.0:
        confidence = OffsetConfidence.MEDIUM
    else:
        confidence = OffsetConfidence.LOW

    # LOW時の3分岐
    if confidence == OffsetConfidence.LOW:
        if valid_count == 0:
            applied = 0.0
            logger.warning("LOW信頼度（候補なし）: offset=0")
        else:
            applied = final_offset
            logger.warning(
                f"LOW信頼度（不安定）: offset={final_offset:.2f}秒を暫定適用。"
                "手動確認を推奨します"
            )
    else:
        applied = final_offset
        logger.info(f"オフセット確定: {final_offset:.2f}秒 (信頼度: {confidence.value})")

    # Phase 4: ドリフト検出
    drift_detected = False
    drift_delta = None
    if "head" in window_deltas and "tail" in window_deltas:
        head_deltas = window_deltas["head"]
        tail_deltas = window_deltas["tail"]
        if head_deltas and tail_deltas:
            head_med = median(head_deltas)
            tail_med = median(tail_deltas)
            drift_delta = abs(tail_med - head_med)
            if drift_delta > 1.0:
                drift_detected = True
                logger.warning(f"ドリフト検出: 冒頭={head_med:.2f}s, 終盤={tail_med:.2f}s, 差={drift_delta:.2f}s")

    # 候補上位
    # （簡易実装: 全deltaの分布からは単一候補のみ）
    top_candidates = [
        {"offset_sec": round(final_offset, 3), "score": 0.0, "pair_count": valid_count}
    ]

    return OffsetResult(
        estimated_offset_sec=round(final_offset, 3),
        confidence=confidence,
        valid_pairs=valid_count,
        total_pairs_before_filter=total_pairs,
        mad=round(mad_val, 4),
        std_dev=round(final_std, 4),
        method="auto",
        sample_windows_used=[w[0] for w in windows],
        drift_detected=drift_detected,
        drift_delta_sec=round(drift_delta, 3) if drift_delta is not None else None,
        top_candidates=top_candidates,
        applied_offset_sec=round(applied, 3),
        override=None,
        excluded_vtt_cues=0,  # apply_offset で更新
    )


def apply_offset(vtt_cues: list[Cue], offset_sec: float) -> tuple[list[Cue], int]:
    """VTTキューにオフセット補正を適用する。

    Args:
        vtt_cues: Zoom VTTキューのリスト
        offset_sec: オフセット値（秒）。VTTの時刻から引く

    Returns:
        (補正済みキューのリスト, 除外されたキュー数)
    """
    corrected = []
    excluded = 0

    for cue in vtt_cues:
        new_start = cue.start - offset_sec
        new_end = cue.end - offset_sec
        if new_end <= 0:
            excluded += 1
            continue
        corrected.append(Cue(
            index=cue.index,
            start=max(0.0, new_start),
            end=new_end,
            speaker=cue.speaker,
            text=cue.text,
        ))

    logger.info(
        f"オフセット補正適用: {offset_sec:.2f}秒, "
        f"{len(corrected)}キュー有効, {excluded}キュー除外"
    )
    return corrected, excluded

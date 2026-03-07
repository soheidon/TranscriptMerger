"""チャンク分割のテスト。"""

import pytest
from src.models import Cue
from src.chunker import split_into_chunks


def _make_id_cue_pairs(count: int, duration_each: float = 5.0):
    """テスト用の (ID, Cue) ペアを生成する。"""
    pairs = []
    for i in range(count):
        uid = f"U{i + 1:06d}"
        cue = Cue(
            index=i + 1,
            start=i * duration_each,
            end=(i + 1) * duration_each - 0.5,
            speaker=f"SPEAKER_{i % 2:02d}",
            text=f"テスト発言{i + 1}",
        )
        pairs.append((uid, cue))
    return pairs


class TestChunking:
    """チャンク分割のテスト。"""

    def test_short_recording_single_chunk(self):
        """3分の録音 → 1チャンク"""
        pairs = _make_id_cue_pairs(36, duration_each=5.0)  # 180秒
        config = {
            "target_duration_sec": 300,
            "search_window_stage1_sec": 180,
            "search_window_stage2_sec": 300,
            "gap_threshold_sec": 1.2,
            "overlap_sec": 15,
        }
        chunks = split_into_chunks(pairs, [], config)
        assert len(chunks) == 1

    def test_30min_recording(self):
        """30分の録音 → 約6チャンク"""
        pairs = _make_id_cue_pairs(360, duration_each=5.0)  # 1800秒
        config = {
            "target_duration_sec": 300,
            "search_window_stage1_sec": 180,
            "search_window_stage2_sec": 300,
            "gap_threshold_sec": 1.2,
            "overlap_sec": 15,
        }
        chunks = split_into_chunks(pairs, [], config)
        assert 4 <= len(chunks) <= 8  # 目安

    def test_no_overlap_on_first_chunk(self):
        """先頭チャンクには前オーバーラップなし"""
        pairs = _make_id_cue_pairs(120, duration_each=5.0)
        config = {
            "target_duration_sec": 300,
            "search_window_stage1_sec": 180,
            "search_window_stage2_sec": 300,
            "gap_threshold_sec": 1.2,
            "overlap_sec": 15,
        }
        chunks = split_into_chunks(pairs, [], config)
        assert len(chunks[0].context_before) == 0

    def test_empty_input(self):
        """空入力 → 空リスト"""
        chunks = split_into_chunks([], [], {"target_duration_sec": 300})
        assert chunks == []

    def test_ids_no_gap(self):
        """全チャンクのIDを結合すると入力全体をカバーする"""
        pairs = _make_id_cue_pairs(120, duration_each=5.0)
        config = {
            "target_duration_sec": 300,
            "search_window_stage1_sec": 180,
            "search_window_stage2_sec": 300,
            "gap_threshold_sec": 1.2,
            "overlap_sec": 15,
        }
        chunks = split_into_chunks(pairs, [], config)
        all_ids = []
        for chunk in chunks:
            all_ids.extend(chunk.srt_ids)
        input_ids = [uid for uid, _ in pairs]
        assert all_ids == input_ids

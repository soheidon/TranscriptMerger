"""オフセット検出のテスト。"""

import pytest
from src.models import Cue, OffsetConfidence
from src.offset import detect_offset, apply_offset


def _make_cues(texts_and_times: list[tuple[str, float, float]]) -> list[Cue]:
    """テスト用のCueリストを生成する。"""
    return [
        Cue(index=i, start=s, end=e, speaker="SPEAKER_00", text=t)
        for i, (t, s, e) in enumerate(texts_and_times)
    ]


class TestApplyOffset:
    """オフセット適用のテスト。"""

    def test_positive_offset(self):
        cues = _make_cues([("hello", 10.0, 12.0), ("world", 15.0, 17.0)])
        result, excluded = apply_offset(cues, 5.0)
        assert len(result) == 2
        assert result[0].start == pytest.approx(5.0)
        assert excluded == 0

    def test_negative_timestamp_excluded(self):
        cues = _make_cues([("hello", 2.0, 4.0), ("world", 10.0, 12.0)])
        result, excluded = apply_offset(cues, 5.0)
        assert len(result) == 1
        assert excluded == 1

    def test_zero_offset(self):
        cues = _make_cues([("hello", 5.0, 7.0)])
        result, excluded = apply_offset(cues, 0.0)
        assert len(result) == 1
        assert result[0].start == pytest.approx(5.0)


class TestDetectOffset:
    """オフセット検出のテスト。"""

    def test_skip_mode(self):
        result = detect_offset([], [], {"mode": "skip"})
        assert result.applied_offset_sec == 0.0
        assert result.method == "skip"

    def test_manual_mode(self):
        result = detect_offset([], [], {"mode": "manual", "manual_offset_sec": 10.0})
        assert result.applied_offset_sec == 10.0
        assert result.method == "manual"

    def test_identical_cues_zero_offset(self):
        """同一テキスト・同一タイミングならoffset≈0"""
        texts = [
            ("こんにちは今日はよろしくお願いします", 0.0, 3.0),
            ("はいよろしくお願いいたします", 3.5, 6.0),
            ("それでは議題に入りましょう", 7.0, 10.0),
            ("最初の議題は予算についてです", 11.0, 14.0),
            ("はい予算の件ですね", 15.0, 18.0),
        ]
        srt_cues = _make_cues(texts)
        vtt_cues = _make_cues(texts)

        config = {
            "mode": "auto",
            "sample_windows": ["head"],
            "window_duration_sec": 300,
            "vtt_search_margin_sec": 300,
            "similarity_threshold": 0.5,
            "min_valid_pairs": 3,
            "mad_k": 3.0,
            "max_offset_sec": 600,
            "use_reading_normalization": False,
        }
        result = detect_offset(srt_cues, vtt_cues, config)
        assert abs(result.applied_offset_sec) < 1.0

    def test_known_offset(self):
        """既知のオフセット（+10秒）が正しく検出されるか"""
        texts = [
            ("こんにちは今日はよろしくお願いします", 0.0, 3.0),
            ("はいよろしくお願いいたします", 3.5, 6.0),
            ("それでは議題に入りましょう", 7.0, 10.0),
            ("最初の議題は予算についてです", 11.0, 14.0),
            ("はい予算の件ですね", 15.0, 18.0),
        ]
        srt_cues = _make_cues(texts)
        # VTT側は+10秒ずれている
        vtt_cues = _make_cues([
            (t, s + 10.0, e + 10.0) for t, s, e in texts
        ])

        config = {
            "mode": "auto",
            "sample_windows": ["head"],
            "window_duration_sec": 300,
            "vtt_search_margin_sec": 300,
            "similarity_threshold": 0.5,
            "min_valid_pairs": 3,
            "mad_k": 3.0,
            "max_offset_sec": 600,
            "use_reading_normalization": False,
        }
        result = detect_offset(srt_cues, vtt_cues, config)
        assert abs(result.applied_offset_sec - 10.0) < 2.0

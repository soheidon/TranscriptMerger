"""単一VTTモードのテスト。"""

import pytest

from src.config_loader import DEFAULTS, deep_merge, resolve_paths
from src.llm_client import build_prompt
from src.models import Chunk, Cue
from src.offset import build_no_secondary_offset_result


def _make_chunk() -> Chunk:
    cue1 = Cue(index=1, start=0.0, end=2.0, speaker="SPEAKER_00", text="えー今日は会議です")
    cue2 = Cue(index=2, start=2.1, end=4.0, speaker="SPEAKER_01", text="はいお願いします")
    return Chunk(
        index=0,
        srt_cues=[cue1, cue2],
        vtt_cues=[],
        srt_ids=["U000001", "U000002"],
        time_range=(0.0, 4.0),
        context_before=[],
        context_after=[],
    )


def test_resolve_paths_single_vtt_flag(tmp_path):
    """use_secondary_vtt: false が resolve に反映される。"""
    config = deep_merge(DEFAULTS, {"input": {"use_secondary_vtt": False}})
    resolved = resolve_paths(config, tmp_path)
    assert resolved["_resolved"]["use_secondary_vtt"] is False


def test_build_prompt_single_vtt_mode_excludes_zoom_section():
    """単一VTTモードでは Zoom セクションと AB_MISMATCH 禁止が含まれる。"""
    chunk = _make_chunk()
    prompt = build_prompt(
        chunk=chunk,
        id_cue_pairs=list(zip(chunk.srt_ids, chunk.srt_cues)),
        use_secondary_vtt=False,
    )
    assert "主VTT（Whisper+pyannote）のみを整形してください" in prompt
    assert "=== Zoom VTT（補助データ・参照ラベルは出力に使用禁止） ===" not in prompt
    assert "source は常に PRIMARY を使用すること" in prompt
    assert "uncertain_reason=AB_MISMATCH は使用しないこと" in prompt


def test_build_prompt_dual_vtt_mode_includes_zoom_section():
    """2本VTTモードで vtt_cues がある場合 Zoom セクションが含まれる。"""
    chunk = _make_chunk()
    chunk.vtt_cues = [
        Cue(index=1, start=0.0, end=2.0, speaker=None, text="今日は会議です")
    ]
    prompt = build_prompt(
        chunk=chunk,
        id_cue_pairs=list(zip(chunk.srt_ids, chunk.srt_cues)),
        use_secondary_vtt=True,
    )
    assert "主VTT（Whisper+pyannote）とZoom VTT（補助データ）を統合・整形してください" in prompt
    assert "=== Zoom VTT（補助データ・参照ラベルは出力に使用禁止） ===" in prompt


def test_build_no_secondary_offset_result():
    """補助VTT未使用時のオフセット結果の形を確認。"""
    result = build_no_secondary_offset_result()
    assert result.applied_offset_sec == 0.0
    assert result.method == "single_vtt"
    assert result.excluded_vtt_cues == 0

    result_skip = build_no_secondary_offset_result(method="skip")
    assert result_skip.method == "skip"

"""VTTパーサーのテスト。"""

import pytest
from pathlib import Path
from src.parser import parse_vtt, parse_timestamp_vtt, extract_speaker_from_text


class TestTimestampParsing:
    """タイムスタンプのパーステスト。"""

    def test_srt_timestamp(self):
        assert parse_timestamp_vtt("00:01:23.456") == pytest.approx(83.456, abs=0.001)

    def test_srt_timestamp_zero(self):
        assert parse_timestamp_vtt("00:00:00.000") == 0.0

    def test_vtt_timestamp_with_hours(self):
        assert parse_timestamp_vtt("00:01:23.456") == pytest.approx(83.456, abs=0.001)

    def test_vtt_timestamp_without_hours(self):
        assert parse_timestamp_vtt("01:23.456") == pytest.approx(83.456, abs=0.001)


class TestSpeakerExtraction:
    """話者ラベル抽出のテスト。"""

    def test_with_speaker(self):
        speaker, text = extract_speaker_from_text("SPEAKER_00: こんにちは")
        assert speaker == "SPEAKER_00"
        assert text == "こんにちは"

    def test_without_speaker(self):
        speaker, text = extract_speaker_from_text("こんにちは")
        assert speaker is None
        assert text == "こんにちは"

    def test_with_japanese_colon(self):
        speaker, text = extract_speaker_from_text("SPEAKER_01：テスト")
        assert speaker == "SPEAKER_01"
        assert text == "テスト"


class TestPrimaryVTTParsing:
    """主VTTファイルのパーステスト。"""

    def test_basic_primary_vtt(self, tmp_path):
        srt_content = """1
00:00:01,000 --> 00:00:03,240
SPEAKER_00: こんにちは

2
00:00:03,500 --> 00:00:07,120
SPEAKER_01: ありがとうございます
"""
        srt_file = tmp_path / "test.srt"
        srt_file.write_text(srt_content, encoding="utf-8")

        cues = parse_vtt(srt_file)
        assert len(cues) == 2
        assert cues[0].speaker == "SPEAKER_00"
        assert cues[0].text == "こんにちは"
        assert cues[0].start == pytest.approx(1.0, abs=0.001)
        assert cues[1].speaker == "SPEAKER_01"


class TestVTTParsing:
    """VTTファイルのパーステスト。"""

    def test_basic_vtt(self, tmp_path):
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:03.240
こんにちは

00:00:03.500 --> 00:00:07.120
ありがとうございます
"""
        vtt_file = tmp_path / "test.vtt"
        vtt_file.write_text(vtt_content, encoding="utf-8")

        cues = parse_vtt(vtt_file)
        assert len(cues) == 2
        assert cues[0].text == "こんにちは"
        assert cues[0].start == pytest.approx(1.0, abs=0.001)

    def test_vtt_with_voice_tag(self, tmp_path):
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:03.240
<v SPEAKER_00>こんにちは</v>
"""
        vtt_file = tmp_path / "test.vtt"
        vtt_file.write_text(vtt_content, encoding="utf-8")

        cues = parse_vtt(vtt_file)
        assert len(cues) == 1
        assert cues[0].speaker == "SPEAKER_00"
        assert cues[0].text == "こんにちは"

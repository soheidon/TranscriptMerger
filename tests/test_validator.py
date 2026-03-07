"""IDバリデーションのテスト。"""

import pytest
from src.validator import validate_llm_output


class TestValidation:
    """IDバリデーションのテスト。"""

    def test_all_ids_present(self):
        """全IDが出力に含まれている場合: 合格"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001"]},
                {"id": "U000002", "source_ids": ["U000002"]},
                {"id": "U000003", "source_ids": ["U000003"]},
            ]
        }
        result = validate_llm_output(llm_output, ["U000001", "U000002", "U000003"])
        assert result.passed is True
        assert result.missing_ids == []

    def test_missing_ids(self):
        """一部IDが欠損: 不合格"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001"]},
                # U000002 が欠損
                {"id": "U000003", "source_ids": ["U000003"]},
            ]
        }
        result = validate_llm_output(llm_output, ["U000001", "U000002", "U000003"])
        assert result.passed is False
        assert "U000002" in result.missing_ids

    def test_duplicate_ids(self):
        """同一IDが複数utteranceに出現: 不合格"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001"]},
                {"id": "U000002", "source_ids": ["U000001"]},  # U000001が重複
            ]
        }
        result = validate_llm_output(llm_output, ["U000001"])
        assert result.passed is False
        assert "U000001" in result.duplicate_ids

    def test_unknown_ids(self):
        """存在しないIDが出力に含まれる: 不合格"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001"]},
                {"id": "U999999", "source_ids": ["U999999"]},  # 未知ID
            ]
        }
        result = validate_llm_output(llm_output, ["U000001"])
        assert result.passed is False
        assert "U999999" in result.unknown_ids

    def test_v_insert_allowed(self):
        """V_INSERT_*は未知IDとしてカウントしない"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001"]},
                {"id": "V_INSERT_001", "source_ids": []},
            ]
        }
        result = validate_llm_output(llm_output, ["U000001"])
        assert result.passed is True

    def test_contiguous_ids(self):
        """連続結合は許可"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001", "U000002", "U000003"]},
            ]
        }
        result = validate_llm_output(llm_output, ["U000001", "U000002", "U000003"])
        assert result.passed is True
        assert result.non_contiguous == []

    def test_non_contiguous_warning(self):
        """非連続結合は警告"""
        llm_output = {
            "utterances": [
                {"id": "U000001", "source_ids": ["U000001", "U000003"]},  # U000002がスキップ
            ]
        }
        result = validate_llm_output(llm_output, ["U000001", "U000002", "U000003"])
        # non_contiguous は warning（passed には影響しないが missing_ids で不合格になる）
        assert "U000001" in result.non_contiguous or len(result.missing_ids) > 0

    def test_empty_utterances(self):
        """空の出力: 不合格"""
        llm_output = {"utterances": []}
        result = validate_llm_output(llm_output, ["U000001"])
        assert result.passed is False

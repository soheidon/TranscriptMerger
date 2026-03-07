"""エンドツーエンドテスト（スタブ）。

実際のLLM APIを呼ぶため、CI/CDではスキップし、手動で実行する。
"""

import pytest


@pytest.mark.skip(reason="LLM APIが必要。手動実行用")
class TestE2E:
    """エンドツーエンドテスト。"""

    def test_basic_pipeline(self, tmp_path):
        """基本的なパイプラインの通しテスト。"""
        # TODO: テスト用のVTTを生成し、パイプラインを実行
        pass

    def test_resume_after_interruption(self, tmp_path):
        """途中停止→再実行のレジュームテスト。"""
        # TODO: 実装
        pass

    def test_best_effort_mode(self, tmp_path):
        """best_effortモードのテスト。"""
        # TODO: 実装
        pass

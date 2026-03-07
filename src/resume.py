"""
レジューム管理。

チャンクごとの中間ファイル保存・読み込み・スキップ判定を担当する。
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ResumeManager:
    """チャンクのレジューム管理を行う。

    Attributes:
        temp_dir: 中間ファイルの保存先ディレクトリ
    """

    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _chunk_path(self, chunk_index: int) -> Path:
        return self.temp_dir / f"temp_chunk_{chunk_index:03d}.json"

    def _meta_path(self, chunk_index: int) -> Path:
        return self.temp_dir / f"temp_chunk_{chunk_index:03d}.meta.json"

    def _error_path(self, chunk_index: int) -> Path:
        return self.temp_dir / f"temp_chunk_{chunk_index:03d}.error.json"

    def is_completed(self, chunk_index: int) -> bool:
        """チャンクが完了済みかどうかを判定する。

        Args:
            chunk_index: チャンク番号

        Returns:
            status=okのmeta.jsonが存在すればTrue
        """
        meta_path = self._meta_path(chunk_index)
        if not meta_path.exists():
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return meta.get("status") == "ok"
        except (json.JSONDecodeError, OSError):
            return False

    def save_result(
        self,
        chunk_index: int,
        llm_output: dict[str, Any],
        meta_info: dict[str, Any],
    ) -> None:
        """チャンクの処理結果を保存する。

        Args:
            chunk_index: チャンク番号
            llm_output: LLM出力のJSON辞書
            meta_info: メタ情報辞書
        """
        # LLM出力
        chunk_path = self._chunk_path(chunk_index)
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(llm_output, f, ensure_ascii=False, indent=2)

        # メタ情報
        meta_info["status"] = "ok"
        meta_info["timestamp"] = datetime.now(timezone.utc).isoformat()
        meta_path = self._meta_path(chunk_index)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=2)

        logger.debug(f"チャンク{chunk_index}の結果を保存: {chunk_path}")

    def save_error(
        self,
        chunk_index: int,
        error: Exception,
        meta_info: dict[str, Any],
    ) -> None:
        """チャンクのエラー情報を保存する。

        Args:
            chunk_index: チャンク番号
            error: 発生した例外
            meta_info: メタ情報辞書
        """
        import traceback

        # エラーファイル
        error_path = self._error_path(chunk_index)
        error_data = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False, indent=2)

        # メタ情報（status=error）
        meta_info["status"] = "error"
        meta_info["timestamp"] = datetime.now(timezone.utc).isoformat()
        meta_path = self._meta_path(chunk_index)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=2)

        logger.debug(f"チャンク{chunk_index}のエラーを保存: {error_path}")

    def load_result(self, chunk_index: int) -> dict[str, Any] | None:
        """保存済みのチャンク結果を読み込む。

        Args:
            chunk_index: チャンク番号

        Returns:
            LLM出力のJSON辞書。存在しない場合はNone
        """
        chunk_path = self._chunk_path(chunk_index)
        if not chunk_path.exists():
            return None
        try:
            with open(chunk_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"チャンク{chunk_index}の読み込み失敗: {e}")
            return None

    def clean(self) -> None:
        """temp/ ディレクトリを初期化する。"""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            logger.info(f"temp/ を初期化しました: {self.temp_dir}")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def get_completion_status(self, total_chunks: int) -> dict[str, list[int]]:
        """全チャンクの完了状態を返す。

        Args:
            total_chunks: 総チャンク数

        Returns:
            {"completed": [...], "failed": [...], "pending": [...]}
        """
        completed = []
        failed = []
        pending = []

        for i in range(total_chunks):
            meta_path = self._meta_path(i)
            if not meta_path.exists():
                pending.append(i)
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                status = meta.get("status")
                if status == "ok":
                    completed.append(i)
                elif status == "error":
                    failed.append(i)
                else:
                    pending.append(i)
            except (json.JSONDecodeError, OSError):
                pending.append(i)

        return {"completed": completed, "failed": failed, "pending": pending}

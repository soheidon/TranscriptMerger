"""
Gemini API プロバイダー実装。

google-generativeai パッケージを使用する。
Structured Outputs (response_schema) で構造化JSONを取得する。
"""

import json
import logging
from typing import Any

from src.providers.base import (
    BaseLLMProvider,
    NonRetryableError,
    RetryableError,
)

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API プロバイダー。"""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._client = None

    def _get_client(self):
        """Geminiクライアントを遅延初期化する。"""
        if self._client is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai.GenerativeModel(self.model)
                logger.info(f"Gemini API初期化完了: model={self.model}")
            except Exception as e:
                raise NonRetryableError(f"Gemini API初期化失敗: {e}") from e
        return self._client

    def call_structured(
        self, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Gemini APIで構造化JSON出力を取得する。

        Args:
            prompt: プロンプト文字列
            schema: 出力JSONスキーマ

        Returns:
            パース済みJSON辞書
        """
        client = self._get_client()

        try:
            response = client.generate_content(
                prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    "temperature": 0.1,  # 再現性を高める
                },
                request_options={"timeout": self.timeout},
            )

            # レスポンスのテキストを取得
            text = response.text
            if not text:
                raise RetryableError("Gemini APIから空のレスポンス")

            # JSONパース
            try:
                result = json.loads(text)
            except json.JSONDecodeError as e:
                raise RetryableError(f"JSONパースエラー: {e}\nraw: {text[:500]}")

            return result

        except RetryableError:
            raise
        except NonRetryableError:
            raise
        except Exception as e:
            error_str = str(e).lower()
            # リトライ可能なエラーの判定
            if any(keyword in error_str for keyword in ["429", "rate limit", "quota"]):
                retry_after = None
                # Retry-Afterヘッダーの抽出を試みる
                raise RetryableError(f"Rate Limit: {e}", retry_after=retry_after)
            elif any(keyword in error_str for keyword in ["500", "503", "timeout", "deadline"]):
                raise RetryableError(f"サーバーエラー: {e}")
            elif any(keyword in error_str for keyword in ["400", "invalid"]):
                raise NonRetryableError(f"リクエストエラー: {e}")
            else:
                raise RetryableError(f"不明なエラー: {e}")

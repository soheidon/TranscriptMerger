"""
プロバイダーの基底クラス。

全プロバイダーはこのインターフェースを実装する。
"""

import abc
import json
import logging
import os
import time
import random
from typing import Any

logger = logging.getLogger(__name__)


class BaseLLMProvider(abc.ABC):
    """LLMプロバイダーの抽象基底クラス。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model = config.get("model", "")
        self.max_retries = config.get("max_retries", 3)
        self.backoff_base = config.get("backoff_base_sec", 2)
        self.timeout = config.get("timeout_sec", 120)
        self.rate_limit_respect = config.get("rate_limit_respect", True)

        # APIキーの取得
        api_key_env = config.get("api_key_env", "")
        self.api_key = os.environ.get(api_key_env, "")
        if not self.api_key:
            logger.warning(f"環境変数 {api_key_env} が設定されていません")

    @abc.abstractmethod
    def call_structured(
        self, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """構造化JSON出力でLLMを呼び出す。

        Args:
            prompt: プロンプト文字列
            schema: 出力JSONスキーマ

        Returns:
            パース済みJSON辞書

        Raises:
            LLMAPIError: API呼び出しに失敗した場合
        """
        ...

    def call_with_retry(
        self, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """リトライ付きでLLMを呼び出す。

        Args:
            prompt: プロンプト文字列
            schema: 出力JSONスキーマ

        Returns:
            パース済みJSON辞書

        Raises:
            LLMAPIError: 全リトライ失敗時
        """
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                result = self.call_structured(prompt, schema)
                return result
            except RetryableError as e:
                last_error = e
                wait = self.backoff_base ** attempt + random.uniform(0, 1)

                # Rate Limit時のRetry-After対応
                if hasattr(e, "retry_after") and e.retry_after and self.rate_limit_respect:
                    wait = max(wait, e.retry_after)

                logger.warning(
                    f"API呼び出し失敗（試行{attempt}/{self.max_retries}）: {e}. "
                    f"{wait:.1f}秒後にリトライ"
                )
                time.sleep(wait)
            except NonRetryableError as e:
                logger.error(f"リトライ不可エラー: {e}")
                raise LLMAPIError(str(e)) from e

        raise LLMAPIError(f"全{self.max_retries}回のリトライに失敗: {last_error}")


class LLMAPIError(Exception):
    """LLM API呼び出しの致命的エラー。"""
    pass


class RetryableError(Exception):
    """リトライ可能なエラー（5xx, 429, タイムアウト等）。"""
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NonRetryableError(Exception):
    """リトライ不可のエラー（400 Bad Request等）。"""
    pass

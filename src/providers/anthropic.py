"""
Anthropic API プロバイダー実装（スタブ）。

将来対応用。現時点では未実装。
"""

import logging
from typing import Any

from src.providers.base import BaseLLMProvider, NonRetryableError

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic API プロバイダー（未実装）。"""

    def call_structured(
        self, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        raise NonRetryableError(
            "Anthropicプロバイダーは未実装です。"
            "config.yaml の api.provider を 'google' に設定してください。"
        )

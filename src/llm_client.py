"""
LLM APIクライアント（プロバイダー抽象化）。

プロバイダー別の実装は src/providers/ に置く。
このモジュールはファクトリとプロンプト構築を担当する。
"""

import json
import logging
from typing import Any

from src.models import Chunk, Cue

logger = logging.getLogger(__name__)


# LLM出力のJSONスキーマ定義
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "utterances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "speaker": {"type": "string"},
                    "text": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["CONTENT", "BACKCHANNEL", "ACK_DECISION"],
                    },
                    "uncertain": {"type": "boolean"},
                    "uncertain_reason": {
                        "type": "string",
                        "enum": ["", "AB_MISMATCH", "LOW_CONFIDENCE", "SPEAKER_AMBIGUOUS", "OVERLAP"],
                    },
                    "uncertain_span_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source": {
                        "type": "string",
                        "enum": ["PRIMARY", "ZOOM", "MERGED"],
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "vtt_supplemented": {"type": "boolean"},
                    "edit_type": {
                        "type": "string",
                        "enum": ["NONE", "NORMALIZE", "VTT_SUPPLEMENT", "UNRESOLVED"],
                    },
                    "edit_note": {"type": "string"},
                },
                "required": [
                    "id", "speaker", "text", "category", "uncertain",
                    "uncertain_reason", "uncertain_span_ids", "source",
                    "source_ids", "vtt_supplemented", "edit_type", "edit_note",
                ],
            },
        },
    },
    "required": ["utterances"],
}


def build_prompt(
    chunk: Chunk,
    id_cue_pairs: list[tuple[str, Cue]],
    dictionary: list[str] | None = None,
) -> str:
    """LLMに送るプロンプトを構築する。

    Args:
        chunk: 処理対象チャンク
        id_cue_pairs: 全(ID, Cue)ペア（オーバーラップ区間の参照用）
        dictionary: 専門用語辞書（オプション）

    Returns:
        プロンプト文字列
    """
    parts = []

    # システム指示
    parts.append("""あなたは文字起こし統合・整形の専門家です。
以下のルールに厳密に従って、主VTT（Whisper+pyannote）とZoom VTT（補助データ）を統合・整形してください。

【絶対ルール】
1. 主VTT（Whisper+pyannote）を正とし、テキスト・話者は主VTTを基本とする
2. Zoom VTTによる補完は、主VTTに該当区間の発話が欠落しており、かつVTTに対応テキストがある場合のみ許可
3. 主VTT・Zoom VTTどちらにも根拠がない情報を絶対に追加しない（捏造禁止）
4. 出力は「処理対象」セクションのIDのみ。前文脈・後文脈のIDは出力しない
5. source_idsは連続するIDのみ結合可。非連続IDの結合や、1つのIDの複数utteranceへの分割は禁止

【発話分類】
- CONTENT: 内容のある発話
- BACKCHANNEL: 相槌・フィラー（「うん」「ええ」「あー」等）
- ACK_DECISION: 承認・合意・意思決定（「はい、それで進めましょう」「了解です」等）。削除禁止

【修正種別（edit_type）】
- NONE: 修正なし
- NORMALIZE: 表記揺れ・誤字の修正のみ
- VTT_SUPPLEMENT: VTT根拠ありの補完
- UNRESOLVED: 判定不能

【不確実理由（uncertain_reason）】
- AB_MISMATCH: 主VTTとZoom VTTの内容が不一致
- LOW_CONFIDENCE: 両方とも聞き取りにくい
- SPEAKER_AMBIGUOUS: 話者の判定が困難
- OVERLAP: 発話が重複
""")

    # 前文脈
    if chunk.context_before:
        parts.append("\n=== 前文脈（参照のみ・出力不要） ===")
        for cue in chunk.context_before:
            speaker = cue.speaker or "UNKNOWN"
            parts.append(f"[CTX] {speaker}: {cue.text}")

    # 主VTT（処理対象）
    parts.append("\n=== 主VTT（Whisper+pyannote・処理対象） ===")
    for uid, cue in zip(chunk.srt_ids, chunk.srt_cues):
        speaker = cue.speaker or "UNKNOWN"
        parts.append(f"[{uid}] {speaker}: {cue.text}")

    # VTT（補助）
    parts.append("\n=== Zoom VTT（補助データ） ===")
    for i, cue in enumerate(chunk.vtt_cues):
        speaker = cue.speaker or ""
        label = f"[V{i + 1:03d}]"
        if speaker:
            parts.append(f"{label} {speaker}: {cue.text}")
        else:
            parts.append(f"{label} {cue.text}")

    # 後文脈
    if chunk.context_after:
        parts.append("\n=== 後文脈（参照のみ・出力不要） ===")
        for cue in chunk.context_after:
            speaker = cue.speaker or "UNKNOWN"
            parts.append(f"[CTX] {speaker}: {cue.text}")

    # 専門用語辞書
    if dictionary:
        parts.append("\n=== 専門用語辞書 ===")
        for term in dictionary:
            parts.append(f"- {term}")

    return "\n".join(parts)


def get_provider(config: dict[str, Any]):
    """設定に基づいてプロバイダーインスタンスを返す。

    Args:
        config: api設定辞書

    Returns:
        プロバイダーインスタンス

    Raises:
        ValueError: 未対応のプロバイダー
    """
    provider_name = config.get("provider", "google")

    if provider_name == "google":
        from src.providers.google import GeminiProvider
        return GeminiProvider(config)
    elif provider_name == "openai":
        from src.providers.openai import OpenAIProvider
        return OpenAIProvider(config)
    elif provider_name == "anthropic":
        from src.providers.anthropic import AnthropicProvider
        return AnthropicProvider(config)
    else:
        raise ValueError(f"未対応のプロバイダー: {provider_name}")

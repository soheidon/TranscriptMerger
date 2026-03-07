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
                    "speaker": {
                        "type": "string",
                        "description": "話者ラベル（例: SPEAKER_00）。判定困難な場合は SPEAKER_UNKNOWN を使用する",
                    },
                    "text": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["CONTENT", "BACKCHANNEL", "ACK_DECISION"],
                    },
                    "uncertain": {"type": "boolean"},
                    "uncertain_reason": {
                        "type": "string",
                        "enum": ["NONE", "AB_MISMATCH", "LOW_CONFIDENCE", "SPEAKER_AMBIGUOUS", "OVERLAP"],
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
    dictionary: list[str] | dict | None = None,
    context_prompt: str | None = None,
) -> str:
    """LLMに送るプロンプトを構築する。

    Args:
        chunk: 処理対象チャンク
        id_cue_pairs: 全(ID, Cue)ペア（オーバーラップ区間の参照用）
        dictionary: 用語辞書（オプション）。単純リスト形式またはカテゴリ別dict形式
        context_prompt: ジョブ固有の背景情報（オプション）

    Returns:
        プロンプト文字列
    """
    parts = []

    # コンテキストプロンプト（ジョブ固有の背景情報）
    if context_prompt:
        parts.append("=== この会議/インタビューの背景情報 ===")
        parts.append(context_prompt)
        parts.append("")

    # システム指示
    parts.append("""あなたは文字起こし統合・整形の専門家です。
以下のルールに厳密に従って、主VTT（Whisper+pyannote）とZoom VTT（補助データ）を統合・整形してください。

【絶対ルール】
1. 主VTT（Whisper+pyannote）を正とし、テキスト・話者は主VTTを基本とする
2. Zoom VTTによる補完は、主VTTに該当区間の発話が欠落しており、かつVTTに対応テキストがある場合のみ許可
3. 主VTT・Zoom VTTどちらにも根拠がない情報を絶対に追加しない（捏造禁止）
4. 出力は「処理対象」セクションのIDのみ。前文脈・後文脈のIDは出力しない
5. source_idsは連続するIDのみ結合可。非連続IDの結合や、1つのIDの複数utteranceへの分割は禁止
6. idフィールドには、入力で与えられた主VTTのID（U000001形式）または V_INSERT_*** のみを使用し、新しい形式のIDを独自に採番してはならない

【話者判定ルール】
1. 主VTTの話者ラベルが最も信頼できる情報源である。主VTTで SPEAKER_00 となっている発話は、原則として SPEAKER_00 のまま出力すること
2. 主VTTで SPEAKER_01 となっているが、文脈上明らかに別話者の応答である場合（質問への返答、相槌、確認応答など）は、SPEAKER_00 に変更してよい。この場合 edit_note にその旨を記録すること
3. この会話に登場する話者は、主VTTに出現する話者ラベル（SPEAKER_00, SPEAKER_01 等）のいずれかである。新しい話者IDを作ってはならない
4. SPEAKER_UNKNOWN は「どの話者か推定する手がかりが本当にゼロの場合」のみ使用すること。文脈や対話パターンから推定できる場合は SPEAKER_UNKNOWN を使わない
5. 「別話者だと推定できるが、具体的にどの話者かは不明」という場合でも、2人会話ならもう一方の話者と判定してよい。多人数会話で区別がつかない場合のみ SPEAKER_UNKNOWN を使うこと
6. 短い応答（「はい」「大丈夫です」「そうですね」等）が質問の直後に来る場合は、質問者と別の話者と判定するのが自然である
7. uncertainフラグと話者判定は独立である。話者を既存の話者ラベル（例: SPEAKER_00）に割り当てたうえで、確信度が低い場合は uncertain=true, uncertain_reason="SPEAKER_AMBIGUOUS" を設定すること。SPEAKER_UNKNOWN にする必要はない
8. 前後が同一話者の長い発話で、その間に1〜3秒程度の短い断片がある場合は、割り込み応答ではなく同一話者の言い淀み・途切れの可能性を考慮すること
9. 3人以上の話者がいる区間では、1〜2語の短い発話だけを独立ターンとして乱立させないこと。前後の発話が同じ話者の説明や自己紹介の一部である場合、途中の短い断片を別話者にしないこと
10. 3人以上の区間で判定が難しい場合も、意味のまとまりを壊してまで細切れにしないこと

【不確実箇所の処理ルール】
1. 聞き取りが不確実でも、テキストを絶対に削除・省略してはならない
2. 推定されるテキストをそのまま text フィールドに入れたうえで uncertain=true を設定すること
3. テキストが空の utterance を出力してはならない。どんなに不明瞭でも、聞こえた内容を最善の推定で記載すること
4. 不確実な箇所が複数の発話にまたがる場合でも、発話単位で分割して各々に uncertain=true を付けること。ひとまとめにして1つの不確実ブロックにしないこと
5. 主VTTと補助VTTが大きく食い違う区間（AB_MISMATCH）では、無理に自然な文に再構成しないこと
6. 不明瞭な音声を、もっともらしい既知の短い語（「ごめん」「そう」「ありがとうございました」等）に安易に変換しないこと。聞こえた音に忠実であることを優先すること
7. AB_MISMATCHが連続する箇所では、個別に短い発話を乱立させず、ひとまとまりの発話として保守的にまとめること

【低確信度時の整形制限】
1. uncertain=true の発話では、自然化・要約・断定化を最小限にすること
2. LOW_CONFIDENCE や AB_MISMATCH の場合、元の言い方が疑問・推量・言い淀みである可能性を保持すること
3. 不確実な発話を、より自然な断定文へ書き換えすぎてはならない
4. 意味が取りきれない場合でも、もっともらしい別文へ言い換えるのではなく、聞こえた内容を保守的に残すこと
5. 疑問文か平叙文か曖昧な場合は、断定文へ寄せず、より保守的な形を優先すること

【発話分類ルール — 機能で判断する】
- CONTENT: 新しい情報・意見・質問・説明・提案を含む発話。「この発話を読んで何か新しいことが分かるか」で判断する
  例: 「3月12日ということで山本さんがOKしてくれた」「井出さんは11も12もどっちも大丈夫?」「ああ、ありがとうございます」
- BACKCHANNEL: 削除しても会話の情報が一切失われない発話。聞き手が話を聞いていることを示すだけのもの
  例: 「うんうん」「ええ」「はい」（単独で、直前に提案や質問がない場合）
  ※ 環境音の誤認識（「よいしょ」等）もBACKCHANNELとする
- ACK_DECISION: この発話を削除すると「合意したのかどうか分からなくなる」もの。提案や質問への明確な回答
  例: 「はい大丈夫です」（提案への承諾）「僕もどっちでもいいです」（意思表明）
ACK_DECISION の判定基準:
- 「はい」単独: 直前が提案・依頼・確認質問なら ACK_DECISION、そうでなければ BACKCHANNEL
- 「そうですね」: 質問への同意ならACK_DECISION、相槌ならBACKCHANNEL
- 環境音・誤認識（「ごちそう」「よいしょ」等）: 必ずBACKCHANNEL
- 挨拶（「よろしくお願いします」等）: 開始・終了の機能を持つ場合はCONTENT

【修正種別（edit_type）】
- NONE: 修正なし
- NORMALIZE: 表記揺れ・誤字の修正のみ
- VTT_SUPPLEMENT: VTT根拠ありの補完
- UNRESOLVED: 判定不能

【テキスト整形ルール】
1. 意味を持たないフィラー語（えー、えっと、あのー、あー、うーん、まあ、そのー、なんか等）は削除すること
2. ただし、相槌として意味がある「はい」「ええ」「そうですね」は削除しないこと
3. 同じ語の繰り返し（「あのあの」「えっとえっと」）は1つに整理すること
4. 言い直し（「3月10日、あ、11日です」）は最終的な内容（「3月11日です」）に整えること。ただし edit_type="NORMALIZE"、edit_note に元の表現を記録すること
5. 文として不自然な途切れ（「であの、ちょっとまたえっと、教えてくださった予定で」）は、意味が通る一文に整えること（例:「教えてくださった予定で日程の選択肢を増やしてみました」）
6. 会話の文脈に全く合わない語（環境音の誤認識と思われるもの、例:「ごちそう」「よいしょ」等）は、category を "BACKCHANNEL" にし、edit_note に「環境音/誤認識の可能性」と記載すること
7. 完全に同一のテキストが連続して出現する場合（「ごめん」「ごめん」等の重複）は、1つに統合してよい。統合した場合は edit_type と edit_note にその旨を記録すること
8. 同一文書内で同じ語の表記を統一すること（例:「お疲れさまです」と「お疲れ様です」が混在する場合はどちらかに寄せる）
9. 同一話者の連続する短い断片が、1つの文の途中で切れている場合は、結合して1文に整形すること。特に「〜を」「〜に」「〜が」等の助詞で終わる断片は、次の断片と結合すべきサインである

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

    # 用語辞書（正しい表記のリスト）
    if dictionary:
        parts.append("\n=== 用語辞書（正しい表記のリスト） ===")
        parts.append("以下は、この会議に登場する可能性がある固有名詞の正しい表記です。")
        parts.append("音声認識による誤変換（読みが近い別の漢字、ひらがな/カタカナ表記、部分的な聞き取り等）を見つけた場合は、このリストの表記に修正してください。")
        parts.append("")

        if isinstance(dictionary, dict):
            # 新フォーマット（カテゴリ付き）
            for category, terms in dictionary.items():
                label = str(category)
                if isinstance(terms, list) and terms:
                    terms_str = "、".join(str(t) for t in terms)
                    parts.append(f"【{label}】{terms_str}")
                else:
                    parts.append(f"【{label}】（なし）")
        elif isinstance(dictionary, list):
            # 旧フォーマット（単純リスト）互換
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

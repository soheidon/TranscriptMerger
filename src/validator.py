"""
IDバリデーション。

LLM出力の各utteranceについて、入力IDとの整合性を検証する。
- ID網羅性（入力IDが全て出力に含まれるか）
- ID重複なし（同一IDが複数utteranceに出現していないか）
- 未知ID排除（出力IDが全て入力IDリストに属するか）
- 連続性（source_ids内のIDが連番で連続しているか）
- オーバーラップ漏出（前後文脈のIDが出力に含まれていないか）
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """バリデーション結果。

    Attributes:
        passed: 全チェック合格か
        missing_ids: 出力に含まれていない入力ID
        duplicate_ids: 複数utteranceに出現しているID
        unknown_ids: 入力リストに存在しないID（V_INSERT_*を除く）
        non_contiguous: source_idsが非連続なutteranceのID
        overlap_leaked: オーバーラップ区間のIDが出力に含まれているケース
        errors: エラーメッセージのリスト（リトライ要因）
        warnings: 警告メッセージのリスト（リトライしない）
    """
    passed: bool = True
    missing_ids: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    non_contiguous: list[str] = field(default_factory=list)
    overlap_leaked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _extract_id_number(uid: str) -> int | None:
    """U000001形式のIDから番号を抽出する。"""
    match = re.match(r"U(\d+)", uid)
    return int(match.group(1)) if match else None


def _is_contiguous(ids: list[str]) -> bool:
    """IDリストが連番で連続しているか判定する。"""
    if len(ids) <= 1:
        return True
    numbers = [_extract_id_number(uid) for uid in ids]
    if any(n is None for n in numbers):
        return True  # V_INSERT_*等が混在する場合は連続性チェック不要
    for i in range(1, len(numbers)):
        if numbers[i] != numbers[i - 1] + 1:
            return False
    return True


def validate_llm_output(
    llm_output: dict,
    expected_ids: list[str],
    context_before_ids: list[str] | None = None,
    context_after_ids: list[str] | None = None,
) -> ValidationResult:
    """LLM出力のIDバリデーションを実行する。

    Args:
        llm_output: LLMが返したJSON辞書
        expected_ids: 本体部分の期待されるIDリスト
        context_before_ids: 前オーバーラップ区間のID（出力に含まれてはいけない）
        context_after_ids: 後オーバーラップ区間のID（出力に含まれてはいけない）

    Returns:
        ValidationResult
    """
    result = ValidationResult()
    context_before_ids = context_before_ids or []
    context_after_ids = context_after_ids or []
    context_ids = set(context_before_ids + context_after_ids)
    expected_set = set(expected_ids)

    utterances = llm_output.get("utterances", [])
    if not utterances:
        result.passed = False
        result.errors.append("utterancesが空です")
        return result

    # 出力に含まれるsource_idsを全て収集
    all_output_source_ids: list[str] = []
    source_id_to_utterance: dict[str, list[str]] = {}

    for utt in utterances:
        utt_id = utt.get("id", "")
        source_ids = utt.get("source_ids", [])

        for sid in source_ids:
            all_output_source_ids.append(sid)
            if sid not in source_id_to_utterance:
                source_id_to_utterance[sid] = []
            source_id_to_utterance[sid].append(utt_id)

    # 1. ID網羅性: 入力IDが全て出力のsource_idsに含まれるか
    output_source_set = set(all_output_source_ids)
    missing = [uid for uid in expected_ids if uid not in output_source_set]
    if missing:
        result.passed = False
        result.missing_ids = missing
        result.errors.append(f"ID欠損: {len(missing)}個のIDが出力に含まれていません: {missing[:5]}")

    # 2. ID重複なし: 同一IDが複数utteranceに出現していないか
    duplicates = [
        sid for sid, utt_list in source_id_to_utterance.items()
        if len(utt_list) > 1 and not sid.startswith("V_INSERT_")
    ]
    if duplicates:
        result.passed = False
        result.duplicate_ids = duplicates
        result.errors.append(f"ID重複: {len(duplicates)}個のIDが複数utteranceに出現: {duplicates[:5]}")

    # 3. 未知ID排除: 出力IDが全て入力IDリストに属するか（V_INSERT_*は除外）
    unknown = [
        sid for sid in output_source_set
        if sid not in expected_set and not sid.startswith("V_INSERT_")
    ]
    if unknown:
        result.passed = False
        result.unknown_ids = unknown
        result.errors.append(f"未知ID: {len(unknown)}個の不明なIDが出力に含まれています: {unknown[:5]}")

    # 4. 連続性: source_ids内のIDが連番で連続しているか
    for utt in utterances:
        source_ids = utt.get("source_ids", [])
        u_ids = [s for s in source_ids if s.startswith("U")]
        if not _is_contiguous(u_ids):
            result.non_contiguous.append(utt.get("id", ""))
            result.warnings.append(
                f"非連続source_ids: utterance {utt.get('id')} の source_ids が連続していません"
            )

    # 5. オーバーラップ漏出: 前後文脈のIDが出力に含まれていないか
    leaked = [sid for sid in all_output_source_ids if sid in context_ids]
    if leaked:
        result.overlap_leaked = leaked
        result.warnings.append(
            f"オーバーラップ漏出: {len(leaked)}個のコンテキストIDが出力に含まれています"
        )

    if result.passed:
        logger.info("IDバリデーション: 合格")
    else:
        logger.warning(f"IDバリデーション: 不合格 ({'; '.join(result.errors)})")

    return result

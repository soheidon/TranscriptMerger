"""
テキスト正規化。

オフセット検出時のテキストマッチングに使用する。
日本語読み変換（pykakasi）はオプション。
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# pykakasiの遅延インポート用
_kakasi_converter = None


def _get_kakasi_converter():
    """pykakasiのコンバーターを遅延初期化して返す。"""
    global _kakasi_converter
    if _kakasi_converter is None:
        try:
            import pykakasi
            kks = pykakasi.kakasi()
            _kakasi_converter = kks
            logger.info("pykakasi を初期化しました")
        except ImportError:
            logger.warning(
                "pykakasi がインストールされていません。"
                "日本語読み正規化は無効です。"
                "有効にするには: pip install pykakasi"
            )
            _kakasi_converter = False  # Noneとの区別用
    return _kakasi_converter if _kakasi_converter is not False else None


def to_reading(text: str) -> str:
    """テキストをひらがな読みに変換する。

    pykakasiが利用可能な場合のみ変換する。
    利用不可の場合は元テキストをそのまま返す。

    Args:
        text: 入力テキスト

    Returns:
        ひらがな変換済みテキスト（or 元テキスト）
    """
    converter = _get_kakasi_converter()
    if converter is None:
        return text

    try:
        result = converter.convert(text)
        return "".join([item["hira"] for item in result])
    except Exception as e:
        logger.debug(f"pykakasi変換エラー: {e}")
        return text


def normalize_for_matching(text: str, use_reading: bool = False) -> str:
    """オフセット検出用にテキストを正規化する。

    Args:
        text: 入力テキスト
        use_reading: 日本語読み変換を使うか

    Returns:
        正規化済みテキスト
    """
    # 1. Unicode正規化（NFKC）
    text = unicodedata.normalize("NFKC", text)

    # 2. 小文字化
    text = text.lower()

    # 3. 句読点・記号の除去
    text = re.sub(r"[。、．，.!！?？…～〜・\-\–\—\"\'「」『』（）()【】\[\]{}]", "", text)

    # 4. フィラー語の除去
    fillers = [
        "えー", "えーと", "あー", "あの", "あのー",
        "まあ", "まー", "うーん", "ええと", "えっと",
        "そのー", "なんか", "こう", "ほら",
    ]
    for filler in fillers:
        text = text.replace(filler, "")

    # 5. 空白の正規化
    text = re.sub(r"\s+", " ", text).strip()

    # 6. 日本語読み変換（オプション）
    if use_reading:
        text = to_reading(text)

    return text


def char_ngrams(text: str, n: int = 3) -> set[str]:
    """テキストから文字n-gramの集合を生成する。

    Args:
        text: 入力テキスト
        n: n-gramのn

    Returns:
        n-gram文字列の集合
    """
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard係数を計算する。

    Args:
        set_a: 集合A
        set_b: 集合B

    Returns:
        Jaccard係数（0.0〜1.0）
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0

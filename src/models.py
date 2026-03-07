"""
共有データモデル定義。

全モジュールで使用するデータクラスをここに集約する。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =============================================================
# パーサー出力
# =============================================================

@dataclass
class Cue:
    """VTTの1キュー（最小単位）。

    Attributes:
        index: 元ファイル内の連番
        start: 開始時刻（秒）
        end: 終了時刻（秒）
        speaker: 話者ラベル（例: "SPEAKER_00"）。不明な場合はNone
        text: テキスト内容
    """
    index: int
    start: float
    end: float
    speaker: Optional[str]
    text: str


# =============================================================
# ID管理
# =============================================================

@dataclass
class IDEntry:
    """ID対応表の1エントリ。

    Attributes:
        id: 不変ID（例: "U000001"）
        start: 開始時刻（秒）
        end: 終了時刻（秒）
        speaker: 話者ラベル
        raw_text: 元テキスト（正規化前）
    """
    id: str
    start: float
    end: float
    speaker: Optional[str]
    raw_text: str


# =============================================================
# チャンク
# =============================================================

@dataclass
class Chunk:
    """LLM処理の1チャンク。

    Attributes:
        index: チャンク番号（0始まり）
        srt_cues: 主VTTキューのリスト（本体部分）
        vtt_cues: Zoom VTTキューのリスト（本体部分、オフセット補正済み）
        srt_ids: 主VTT本体部分のID群
        time_range: 本体部分の時間範囲 (start_sec, end_sec)
        context_before: 前オーバーラップの主VTTキュー群（参照のみ）
        context_after: 後オーバーラップの主VTTキュー群（参照のみ）
    """
    index: int
    srt_cues: list[Cue]
    vtt_cues: list[Cue]
    srt_ids: list[str]
    time_range: tuple[float, float]
    context_before: list[Cue] = field(default_factory=list)
    context_after: list[Cue] = field(default_factory=list)


# =============================================================
# オフセット検出
# =============================================================

class OffsetConfidence(str, Enum):
    """オフセット信頼度。"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class OffsetResult:
    """オフセット検出結果。

    Attributes:
        estimated_offset_sec: 推定オフセット値（秒）
        confidence: 信頼度（HIGH/MEDIUM/LOW）
        valid_pairs: 有効ペア数
        total_pairs_before_filter: フィルタ前の総ペア数
        mad: MAD値
        std_dev: 標準偏差
        method: 検出方法（"auto" or "manual" or "skip"）
        sample_windows_used: 使用したサンプル窓
        drift_detected: ドリフト検出の有無
        drift_delta_sec: ドリフト量（秒）
        top_candidates: オフセット候補上位リスト
        applied_offset_sec: 最終的に適用されたオフセット値
        override: 手動オーバーライド値（Noneなら自動）
        excluded_vtt_cues: 補正後に除外されたVTTキュー数
    """
    estimated_offset_sec: float
    confidence: OffsetConfidence
    valid_pairs: int
    total_pairs_before_filter: int
    mad: float
    std_dev: float
    method: str
    sample_windows_used: list[str]
    drift_detected: bool
    drift_delta_sec: Optional[float]
    top_candidates: list[dict]
    applied_offset_sec: float
    override: Optional[float]
    excluded_vtt_cues: int


# =============================================================
# LLM出力
# =============================================================

class Category(str, Enum):
    """発話分類。"""
    CONTENT = "CONTENT"
    BACKCHANNEL = "BACKCHANNEL"
    ACK_DECISION = "ACK_DECISION"


class UncertainReason(str, Enum):
    """不確実理由。"""
    NONE = "NONE"
    AB_MISMATCH = "AB_MISMATCH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    SPEAKER_AMBIGUOUS = "SPEAKER_AMBIGUOUS"
    OVERLAP = "OVERLAP"


class EditType(str, Enum):
    """修正種別。"""
    NONE = "NONE"
    NORMALIZE = "NORMALIZE"
    VTT_SUPPLEMENT = "VTT_SUPPLEMENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class Utterance:
    """LLM出力の1発話。

    Attributes:
        id: 発話ID（U000001 or V_INSERT_001）
        speaker: 話者ラベル（例: "SPEAKER_00"）。判定困難な場合は "SPEAKER_UNKNOWN"
        text: 整形済みテキスト
        category: 発話分類
        uncertain: 聞き取り不確実フラグ
        uncertain_reason: 不確実理由
        uncertain_span_ids: 不確実区間のID群
        source: 根拠ソース（PRIMARY/ZOOM/MERGED）
        source_ids: 元主VTTキューのID群
        vtt_supplemented: VTT補完フラグ
        edit_type: 修正種別
        edit_note: 修正内容の説明
        start: 開始時刻（Python側で復元）
        end: 終了時刻（Python側で復元）
    """
    id: str
    speaker: str
    text: str
    category: Category
    uncertain: bool
    uncertain_reason: UncertainReason
    uncertain_span_ids: list[str]
    source: str
    source_ids: list[str]
    vtt_supplemented: bool
    edit_type: EditType
    edit_note: str
    # Python側で復元するフィールド（LLM出力には含まれない）
    start: Optional[float] = None
    end: Optional[float] = None

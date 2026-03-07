"""
ID付与・対応表管理。

主VTTキューに不変ID（U000001形式）を付与し、
ID→(start, end, speaker, text)の対応表を管理する。
"""

import logging
from typing import Optional

from src.models import Cue, IDEntry

logger = logging.getLogger(__name__)


class IDManager:
    """主VTTキューのID付与と対応表の管理を行う。

    Attributes:
        _index: ID対応表（メイン）
        _vtt_insert_index: VTT補完キュー用の補助テーブル
        _counter: 次に付与するID番号
        _vtt_insert_counter: VTT補完用の次の番号
    """

    def __init__(self) -> None:
        self._index: dict[str, IDEntry] = {}
        self._vtt_insert_index: dict[str, dict] = {}
        self._counter: int = 1
        self._vtt_insert_counter: int = 1

    def assign_ids(self, srt_cues: list[Cue]) -> list[tuple[str, Cue]]:
        """主VTTキューにIDを付与する。

        Args:
            srt_cues: 主VTTキューのリスト

        Returns:
            (ID, Cue) のペアのリスト
        """
        results = []
        for cue in srt_cues:
            uid = f"U{self._counter:06d}"
            self._index[uid] = IDEntry(
                id=uid,
                start=cue.start,
                end=cue.end,
                speaker=cue.speaker,
                raw_text=cue.text,
            )
            results.append((uid, cue))
            self._counter += 1

        logger.info(f"ID付与完了: {len(results)}キュー（U000001〜U{self._counter - 1:06d}）")
        return results

    def register_vtt_insert(
        self, insert_id: str, start: float, end: float, original_text: str
    ) -> None:
        """VTT補完キューを補助テーブルに登録する。

        Args:
            insert_id: V_INSERT_XXX形式のID
            start: 開始時刻（秒、オフセット補正済み）
            end: 終了時刻（秒、オフセット補正済み）
            original_text: VTTの元テキスト
        """
        self._vtt_insert_index[insert_id] = {
            "start": start,
            "end": end,
            "vtt_original_text": original_text,
        }

    def get_entry(self, uid: str) -> Optional[IDEntry]:
        """IDからエントリを取得する。

        Args:
            uid: U000001形式のID

        Returns:
            IDEntryまたはNone
        """
        return self._index.get(uid)

    def get_vtt_insert(self, insert_id: str) -> Optional[dict]:
        """VTT補完IDからエントリを取得する。

        Args:
            insert_id: V_INSERT_XXX形式のID

        Returns:
            補助テーブルのエントリまたはNone
        """
        return self._vtt_insert_index.get(insert_id)

    def get_time_range(self, uids: list[str]) -> Optional[tuple[float, float]]:
        """ID群の時間範囲を返す。

        Args:
            uids: IDのリスト

        Returns:
            (start, end) または None
        """
        starts = []
        ends = []
        for uid in uids:
            if uid.startswith("V_INSERT_"):
                entry = self._vtt_insert_index.get(uid)
                if entry:
                    starts.append(entry["start"])
                    ends.append(entry["end"])
            else:
                entry = self._index.get(uid)
                if entry:
                    starts.append(entry.start)
                    ends.append(entry.end)

        if not starts:
            return None
        return min(starts), max(ends)

    def get_all_ids(self) -> list[str]:
        """全IDを付与順に返す。"""
        return list(self._index.keys())

    @property
    def size(self) -> int:
        """登録済みID数を返す。"""
        return len(self._index)

    def to_dict(self) -> dict:
        """対応表をシリアライズ可能な辞書として返す。"""
        return {
            uid: {
                "start": entry.start,
                "end": entry.end,
                "speaker": entry.speaker,
                "raw_text": entry.raw_text,
            }
            for uid, entry in self._index.items()
        }

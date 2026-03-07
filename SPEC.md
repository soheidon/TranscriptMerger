# Transcript-Merger-AI 仕様書 v3.2

**最終更新: 2026-03-07**

---

## 改訂履歴

| バージョン | 日付 | 変更内容 |
|-----------|------|----------|
| v2.2 | — | 初版作成 |
| v3.0 | 2026-03-07 | 録音オフセット検出・補正機能追加、設定ファイル仕様追加、エラー処理拡充、不確実理由enum追加、全体構成リファクタリング |
| v3.1 | 2026-03-07 | LLMプロバイダー抽象化、オフセット検出の複数窓方式化・MAD導入・日本語読み正規化、チャンク境界オーバーラップ導入、source_ids変換規則の明文化、IDバリデーション追加、スキーマ修正（vtt_supplemented/edit_type/enum厳密化/chunk_summary任意化）、部分成功モード追加、テスト仕様拡充 |
| v3.2 | 2026-03-07 | Whisper+pyannoteの入力形式をSRTからVTTに変更（両入力がVTT形式に統一）。用語を「主VTT／Zoom VTT」で一貫化、source enumをPRIMARY/ZOOMに変更、configキー名をprimary_vtt_path/zoom_vtt_pathに変更 |

---

## 1. システム概要

### 1.1 目的

Zoomクラウド録画（VTT）とローカル高精度AI（Whisper + pyannote による VTT）の2つの文字起こしデータを統合し、LLMで比較・補完・クレンジング（誤字修正、フィラー削除、相槌判定など）を行う。

長時間会議でもAPIの出力制限・タイムアウトを回避するため、タイムスタンプ基準のチャンク分割で順次処理し、途中失敗時もレジューム可能な堅牢なバッチ処理ツールとして設計する。

### 1.2 設計原則

| 原則 | 説明 |
|------|------|
| 主VTT正・Zoom VTT補助 | Whisper+pyannote（主VTT）を主データとし、Zoom（VTT）は欠落補完のみに使用する |
| 捏造禁止 | 主VTT・Zoom VTTどちらにも根拠がない情報の追加を一切禁止する |
| LLMにタイムスタンプを触らせない | LLMの入出力はIDベースに限定し、タイムスタンプはPython側で復元する |
| 冪等・再開可能 | チャンク単位で中間結果を保存し、完了済みチャンクはスキップする |
| 録音ズレに耐性を持つ | 2ソース間のタイムオフセットを自動推定し、補正した上で突合する |
| LLMプロバイダー非依存 | 仕様はLLM抽象で記述し、特定APIに依存しない。初期実装のプロバイダーはconfigで指定する |

---

## 2. 全体処理フロー

以下の7ステップで処理を行う。

```
[入力]
  whisper_output.vtt  ─┐
  zoom_output.vtt     ─┤
  (専門用語辞書)       ─┤
  (speaker_map.json)  ─┘
         │
         ▼
  ┌─────────────────────────────────────────┐
  │ Step 1: データパース                     │
  │   主VTT / Zoom VTT → 内部表現に変換            │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │ Step 2: 録音オフセット検出・補正         │
  │   複数窓テキストマッチング               │
  │   → offset_sec を確定                   │
  │   → VTT 全タイムスタンプを補正           │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │ Step 3: ID付与（不変キー生成）           │
  │   主VTT キューに U000001〜 を連番付与      │
  │   ID → (start, end, speaker, text)      │
  │   の対応表をメモリ保持                   │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │ Step 4: チャンク分割（同期スプリット）    │
  │   主VTT タイムスタンプ基準で 5分分割       │
  │   前後オーバーラップ区間を付与            │
  │   VTT を最近傍タイムスタンプで同期        │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │ Step 5: LLM 順次処理                     │
  │   チャンクごとに構造化JSON出力を取得      │
  │   + IDバリデーション                     │
  │   + レジューム用の中間ファイル保存        │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │ Step 6: 最終生成                         │
  │   全チャンクJSON結合（オーバーラップ除去） │
  │   ID対応表からタイムスタンプ復元          │
  │   → TXT / SRT / VTT / JSON 出力         │
  └─────────────────────────────────────────┘
```

### 2.1 データパース

`whisper_output.vtt` と `zoom_output.vtt` を読み込み、内部表現（開始・終了・話者・テキスト）へ変換する。主VTT（Whisper+pyannote）を正（主データ）、Zoom VTTを補助（網羅性補完）とする。

### 2.2 録音オフセット検出・補正

主VTTとZoom VTTの録音開始時刻がずれている場合に、複数窓テキストマッチングによりオフセットを推定し補正する。詳細は第3章を参照。

### 2.3 ID付与（不変キー生成）

主VTTの最小単位（キュー/セグメント）ごとに不変ID（`U000001` 形式の連番）を付与する。`ID → (start, end, speaker, raw_text)` の対応表をPython側で保持する。詳細は第4章を参照。

### 2.4 チャンク分割（同期スプリット）

主VTTのタイムスタンプを基準に目標間隔5分（300秒）で分割し、各チャンクに前後オーバーラップ区間を付与する。Zoom VTT側はオフセット補正済みのタイムスタンプで同期して切り分ける。詳細は第5章を参照。

### 2.5 LLM順次処理（構造化JSON出力）

チャンク単位でLLM APIにリクエストを送る。出力は構造化JSON（スキーマ強制）で取得し、LLMにはタイムスタンプを出力させず、必ずID中心の出力とする。返却後にIDバリデーションを行う。詳細は第7章・第8章を参照。

### 2.6 一時保存（レジューム）

チャンクごとに `temp_chunk_XXX.json` と `temp_chunk_XXX.meta.json` を即時保存する。`meta.json` の `status=ok` を完了条件とし、完了済みチャンクは再実行時にスキップする。詳細は第10章を参照。

### 2.7 最終生成（Python側で復元）

すべてのチャンクJSONを結合し（オーバーラップ区間は中央チャンクの結果を採用）、ID対応表に照合してタイムスタンプを復元する。最終成果物として TXT + SRT + VTT + JSON + offset_report を出力する。詳細は第6章を参照。

---

## 3. 録音オフセット検出・補正

### 3.1 背景と課題

Zoomクラウド録画とローカル録音（Whisper）は、以下の理由で録音開始時刻がずれることがある。

- Zoomクラウド録画は「レコーディング開始」ボタン押下時点から開始
- ローカル録音ソフトは会議参加時点や手動開始時点から開始
- 数秒〜数分のオフセットが発生し、そのまま突合すると対応が崩れる

このオフセットを自動検出し、Zoom VTT側のタイムスタンプを補正した上でチャンク分割・突合を行う。

### 3.2 検出アルゴリズム

#### Phase 1: 複数窓テキストマッチング

先頭だけに依存すると、会議冒頭の雑談・音声チェック・無音でマッチが不安定になる。そのため、録音全体から複数のサンプル窓を取り、照合する。

**サンプル窓の設定:**

| 窓 | 主VTT側の範囲 | Zoom VTT側の探索範囲 | 目的 |
|----|------------|----------------|------|
| 冒頭窓 | 0〜5分 | 0〜15分 | 初期オフセットの大まかな推定 |
| 中盤窓 | 全体の40%〜50%地点の5分間 | 同地点 ± offset候補 ± 5分 | 冒頭の不安定さを回避した検証 |
| 終盤窓 | 全体の80%〜90%地点の5分間 | 同地点 ± offset候補 ± 5分 | ドリフト（時間経過によるズレ拡大）の検出 |

録音が短い（15分未満）場合は冒頭窓のみで処理する。中盤・終盤窓は冒頭窓で得た候補オフセット周辺を重点的に探索する。

**テキスト正規化:**

各キューのテキストに対し、以下の正規化を適用してから比較する。

1. Unicode正規化（NFKC）
2. 句読点・記号の除去
3. 空白の正規化（連続空白を単一スペースに）
4. **日本語読み変換**: pykakasi等を用いてテキストをひらがな（またはカタカナ読み）に変換する。WhisperとZoomのASRエンジン差異による「漢字/ひらがな/カタカナ/アルファベット」の表記ゆれ（例:「ありがとうございます」vs「有難う御座います」、「AI」vs「エーアイ」）を吸収する
5. フィラー語の除去（「えー」「あの」等）

**マッチング手法:**

正規化済みテキスト間の類似度を、以下のハイブリッドスコアで評価する。

| 手法 | 重み | 説明 |
|------|------|------|
| 文字3-gram Jaccard係数 | 0.5 | ASRの微妙な聞き取り差異に頑健 |
| difflib.SequenceMatcher ratio | 0.3 | 語順の一致を評価 |
| キーワード完全一致率 | 0.2 | 数字・固有名詞（辞書がある場合）の一致 |

類似度が `similarity_threshold`（デフォルト: 0.6）以上のペアを有効マッチとする。有効マッチごとにタイムスタンプ差分 `Δt = Zoom_VTT.start − Primary_VTT.start` を計算する。

#### Phase 2: オフセット確定（MADベース外れ値除去）

収集したΔt群から外れ値を除外し、代表値を確定する。

1. 全窓から収集したΔt群の中央値（median）を算出する
2. **MAD（Median Absolute Deviation）** を算出する: `MAD = median(|Δt_i − median(Δt)|)`
3. `|Δt_i − median| > MAD × k`（k = 3.0）の範囲外を外れ値として除外する
4. 残ったΔt群の中央値を最終オフセット値とする
5. 残Δt群の標準偏差を算出し、信頼性判定に使用する

MADは中央値ベースの散らばり指標であり、外れ値に引っ張られにくいため、中央値との組み合わせとして整合的である。

#### Phase 3: 信頼性判定と分岐

信頼性を3段階で判定し、段階に応じた動作を行う。

| 信頼度 | 条件 | 挙動 |
|--------|------|------|
| HIGH | 有効ペア ≥ 5 かつ σ ≤ 1.0秒 | オフセットを自動適用し、ログに記録 |
| MEDIUM | 有効ペア ≥ 3 かつ σ ≤ 2.0秒 | オフセットを適用するが警告ログを出力。ユーザー確認を推奨 |
| LOW | 上記以外 | 下記の3分岐で処理する |

**LOW信頼度時の3分岐:**

LOW判定時に一律 `offset=0` にフォールバックすると、「候補はあるが不安定」なケースでかえって全チャンクがズレるリスクがある。そのため以下の3分岐とする。

| 条件 | 動作 |
|------|------|
| 有効ペアが0件（候補が全くない） | `offset=0` を適用。警告を出力し、手動指定を促す |
| 有効ペアが1〜2件（候補はあるが不安定） | 候補オフセットを暫定適用しつつ `WARNING` レベルで警告。offset_reportに全候補を記録し、ユーザーの事後確認を強く推奨する |
| 手動指定（`--offset-sec`）がある | 手動指定値を最優先で使用 |

#### Phase 4: ドリフト検出（オプション）

冒頭窓と終盤窓のオフセット推定値に有意な差がある（差 > 1.0秒）場合、録音速度のドリフトが発生している可能性がある。この場合は `offset_report.json` に `drift_detected: true` と差分値を記録し、警告を出力する。ドリフト補正（線形補間等）は v3.1 時点では未実装とし、将来対応とする。

### 3.3 オフセット補正の適用

確定したオフセット値 `offset_sec` を以下のように適用する。

- Zoom VTT側の全タイムスタンプを `start − offset_sec`, `end − offset_sec` で補正する
- 補正後に負のタイムスタンプになるセグメントは、Zoom VTT側が録音前の内容を含んでいたことを意味するため、チャンク分割からは除外するが、補完候補としては保持する
- オフセット値、信頼度、有効ペア数、全候補を `offset_report.json` に記録する

### 3.4 手動オーバーライド

コマンドラインオプション:

```
--offset-sec <秒数>   手動でオフセット値を指定（負値可）
--offset-auto         自動検出を実行（デフォルト）
--offset-skip         オフセット検出をスキップ（offset=0扱い）
```

手動指定時はオフセット推定ステップをスキップし、指定値を直接適用する。

### 3.5 オフセット検出パラメータ

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `sample_windows` | head, mid, tail | サンプル窓の種別（短い録音ではheadのみ） |
| `window_duration_sec` | 300（5分） | 各サンプル窓の長さ |
| `vtt_search_margin_sec` | 300（5分） | 中盤・終盤窓でZoom VTT側を探索する追加マージン |
| `similarity_threshold` | 0.6 | ハイブリッドスコアの最低閾値 |
| `min_valid_pairs` | 3 | オフセット確定に必要な最少ペア数 |
| `mad_k` | 3.0 | MAD外れ値除去の倍率 |
| `max_offset_sec` | 600（10分） | 検出するオフセットの上限。超過時は異常と判定 |
| `use_reading_normalization` | true | 日本語読み変換（pykakasi）を使うか |

### 3.6 オフセットレポート（offset_report.json）

```json
{
  "estimated_offset_sec": 12.34,
  "confidence": "HIGH",
  "valid_pairs": 8,
  "total_pairs_before_filter": 12,
  "mad": 0.32,
  "std_dev": 0.45,
  "method": "auto",
  "sample_windows_used": ["head", "mid", "tail"],
  "drift_detected": false,
  "drift_delta_sec": null,
  "top_candidates": [
    {"offset_sec": 12.34, "score": 0.82, "pair_count": 8},
    {"offset_sec": 12.00, "score": 0.78, "pair_count": 6},
    {"offset_sec": -3.50, "score": 0.31, "pair_count": 2}
  ],
  "applied_offset_sec": 12.34,
  "override": null,
  "excluded_vtt_cues": 0
}
```

---

## 4. ID付与仕様

### 4.1 ID体系

主VTTの最小単位（キュー/セグメント）ごとに不変IDを付与する。

- 形式: `U` + ゼロ埋め6桁（例: `U000001`, `U000002`, ...）
- 付与タイミング: パース直後、オフセット補正後・チャンク分割前
- 付与対象: 主VTTキューのみ（VTTキューには付与しない）

### 4.2 ID対応表（インデックス）

Python側でメモリに保持する辞書:

```python
id_index = {
    "U000001": {"start": 0.000, "end": 3.240, "speaker": "SPEAKER_00", "raw_text": "..."},
    "U000002": {"start": 3.500, "end": 7.120, "speaker": "SPEAKER_01", "raw_text": "..."},
}
```

この対応表は最終生成フェーズでタイムスタンプを復元する際の唯一の情報源となる。

### 4.3 VTT補完キュー用の補助テーブル

主VTTに存在せずVTTにのみ存在する発話をLLMが補完挿入する場合、IDは `V_INSERT_001` 形式でLLMが採番する。Python側で、VTTの元タイムスタンプ（オフセット補正済み）をこの補助テーブルに記録する。

```python
vtt_insert_index = {
    "V_INSERT_001": {"start": 45.200, "end": 47.800, "vtt_original_text": "..."},
}
```

---

## 5. チャンク分割仕様

### 5.1 基本パラメータ

| パラメータ | 値 | 説明 |
|-----------|------|------|
| `target_duration_sec` | 300（5分） | 目標チャンク長 |
| `search_window_stage1_sec` | ±180（±3分） | 第1段階探索窓 |
| `search_window_stage2_sec` | ±300（±5分） | 第2段階探索窓（第1で見つからない場合） |
| `gap_threshold_sec` | 1.2 | 字幕間ギャップの閾値（秒）。最小0.8まで調整可 |
| `overlap_sec` | 15 | 前後に付与するオーバーラップ区間（秒） |

### 5.2 分割点の優先順位（主VTT基準）

同一探索窓内に複数の候補がある場合、以下の優先度で選択する。

1. **字幕ギャップ**: 次キューの `start` − 前キューの `end` ≥ `gap_threshold_sec`
2. **話者切替直後**: `speaker[i] ≠ speaker[i-1]` となる境界
3. **文末**: テキストが句点（`。`）、ピリオド（`.`）、改行で終わるキュー
4. **強制分割**: 上記いずれも該当しない場合、ターゲット時刻に最も近いキュー境界

### 5.3 Zoom VTT側の同期

主データ側で分割点が確定したら、その時刻に最も近いVTTキュー境界でVTTを切り分ける。オフセット補正済みのVTTタイムスタンプを使用する。

### 5.4 前後オーバーラップ（文脈保持）

チャンクを完全に独立させてLLMに投げると、境界部分で以下の問題が発生する。

- 直前の話題や代名詞（「これ」「それ」）の指示先がLLMに伝わらない
- 相槌が境界で意味を変える（前の発言への応答なのか、次の発言の一部なのか）
- 話者交替の解釈が前後の文脈に依存する

これを防ぐため、各チャンクに前後のオーバーラップ区間を付与する。

**方式:**

```
チャンクN のLLM入力:
  [前オーバーラップ: 前チャンクの末尾15秒分] ← context_before として明示
  [本体: 分割点〜分割点の300秒]               ← 処理対象
  [後オーバーラップ: 次チャンクの先頭15秒分] ← context_after として明示
```

- LLMへのプロンプトでは、オーバーラップ区間を `=== 前文脈（参照のみ） ===` / `=== 後文脈（参照のみ） ===` と明示し、出力対象は本体部分のIDのみであることを指示する
- 最終採用区間は本体部分のみとし、オーバーラップ区間のLLM出力は破棄する
- 先頭チャンクには前オーバーラップなし、末尾チャンクには後オーバーラップなし

### 5.5 チャンク結果の検証

分割後、以下を検証してログに出力する。

- 各チャンクの実時間長（目標 ±5分以内に収まっているか）
- 主VTTキュー数とVTTキュー数の比率（極端な偏りは警告）
- 隣接チャンク間でキューの重複・欠落がないこと（オーバーラップ区間を除く）

---

## 6. 出力仕様

最終出力は以下の5点セットとする。

### 6.1 TXT（読み物版）

- ファイル: `final_transcript.txt`
- タイムスタンプなし（可読性優先）
- 不確実箇所のみPython側で復元したタイムスタンプを括弧で併記
- `BACKCHANNEL` のうち削除条件（8.2節参照）を満たすものは除外

```
SPEAKER_00
整形済みの発言内容。

SPEAKER_01
次の発言内容。

（聞き取り不確実 00:12:34–00:12:41 / 理由: 主VTTとZoom VTTが不一致）
SPEAKER_00
不確実な区間のテキスト。
```

### 6.2 SRT（レビュー・編集用）

- ファイル: `final_transcript.srt`
- キュー本文先頭に話者ラベルを付与（例: `SPEAKER_00: ...`）
- 相槌を含む全発話を収録（レビュー用）

```
1
00:00:01,000 --> 00:00:03,240
SPEAKER_00: こんにちは、今日は...

2
00:00:03,500 --> 00:00:07,120
SPEAKER_01: ありがとうございます...
```

### 6.3 VTT（Web互換）

- ファイル: `final_transcript.vtt`
- 話者はWebVTT voice tagで表現

```
WEBVTT

00:00:01.000 --> 00:00:03.240
<v SPEAKER_00>こんにちは、今日は...</v>

00:00:03.500 --> 00:00:07.120
<v SPEAKER_01>ありがとうございます...</v>
```

### 6.4 JSON（正本・メタ情報付き）

- ファイル: `final_transcript.json`
- Python側でID対応表からタイムスタンプを復元済みの完全版

```json
{
  "metadata": {
    "generated_at": "2026-03-07T12:00:00Z",
    "source_primary": "whisper_output.vtt",
    "source_vtt": "zoom_output.vtt",
    "applied_offset_sec": 12.34,
    "offset_confidence": "HIGH",
    "total_chunks": 12,
    "total_utterances": 845,
    "llm_provider": "google",
    "llm_model": "gemini-2.0-flash",
    "completion_mode": "strict"
  },
  "utterances": [
    {
      "id": "U000001",
      "start": 1.000,
      "end": 3.240,
      "speaker": "SPEAKER_00",
      "text": "こんにちは、今日は...",
      "category": "CONTENT",
      "uncertain": false,
      "uncertain_reason": "",
      "source": "PRIMARY",
      "source_ids": ["U000001"],
      "edit_type": "NONE",
      "edit_note": ""
    }
  ]
}
```

### 6.5 オフセットレポート

- ファイル: `offset_report.json`
- 内容は3.6節を参照

---

## 7. LLM処理仕様

### 7.1 プロバイダー抽象化

仕様レベルではLLMの具体的なプロバイダーに依存しない。要件は以下の通り。

| 要件 | 説明 |
|------|------|
| 構造化JSON出力 | スキーマを指定して型付きJSONを返却できること |
| 十分な入力コンテキスト長 | 5分チャンク＋前後オーバーラップを処理できること（目安: 入力8,000トークン以上） |
| 日本語処理能力 | 日本語の誤字修正・表記正規化が可能なこと |

**初期実装のプロバイダー設定:**

config の `api.provider` で指定する。各プロバイダー固有の設定（構造化出力の指定方法等）は実装レイヤーで吸収する。

| プロバイダー | 構造化JSON出力の指定方法 |
|-------------|------------------------|
| `google` (Gemini) | `response_mime_type="application/json"` + `response_schema` |
| `openai` | `response_format={"type": "json_schema", "json_schema": {...}}` |
| `anthropic` | システムプロンプトでJSON出力を指示 + 出力バリデーション |

### 7.2 LLMへの入力形式

```
=== 前文脈（参照のみ・出力不要） ===
[U000048] SPEAKER_01: 前チャンク末尾の発言...
[U000049] SPEAKER_00: 前チャンク末尾の発言...

=== 主VTT（Whisper+pyannote・処理対象） ===
[U000050] SPEAKER_00: こんにちは、今日は...
[U000051] SPEAKER_01: ありがとうございます...
...

=== Zoom VTT（補助データ） ===
[V001] こんにちは、今日は...
[V002] ありがとうございます...
...

=== 後文脈（参照のみ・出力不要） ===
[U000098] SPEAKER_00: 次チャンク先頭の発言...
[U000099] SPEAKER_01: 次チャンク先頭の発言...

=== 専門用語辞書 ===
- ABC株式会社
- XYZプロジェクト
...
```

LLMにはタイムスタンプを一切渡さない。入力はID + 話者 + テキストのみ。前文脈・後文脈は「参照のみで出力対象ではない」ことをプロンプトで明示する。

### 7.3 LLM出力JSONスキーマ

以下のスキーマを構造化JSON出力の型定義として使用する。`category` および `uncertain_reason` は文字列型ではなく厳密な `enum` として定義すること。

```json
{
  "type": "object",
  "properties": {
    "utterances": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string",
            "description": "主VTT由来のID（例: U000001）。VTT補完で新規追加した場合は V_INSERT_001 形式"
          },
          "speaker": {
            "type": "string",
            "description": "話者ラベル（例: SPEAKER_00）"
          },
          "text": {
            "type": "string",
            "description": "整形後のテキスト（誤字修正・フィラー除去済み）"
          },
          "category": {
            "type": "string",
            "enum": ["CONTENT", "BACKCHANNEL", "ACK_DECISION"],
            "description": "発話分類"
          },
          "uncertain": {
            "type": "boolean",
            "description": "聞き取り不確実かどうか"
          },
          "uncertain_reason": {
            "type": "string",
            "enum": ["", "AB_MISMATCH", "LOW_CONFIDENCE", "SPEAKER_AMBIGUOUS", "OVERLAP"],
            "description": "不確実の理由。uncertain=falseの場合は空文字"
          },
          "uncertain_span_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "不確実区間に対応するIDの配列。uncertain=falseの場合は空配列"
          },
          "source": {
            "type": "string",
            "enum": ["PRIMARY", "ZOOM", "MERGED"],
            "description": "この発話の根拠ソース"
          },
          "source_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "この発話の元となった主VTTキューのID群。変換規則は7.4節を参照"
          },
          "vtt_supplemented": {
            "type": "boolean",
            "description": "VTTからの補完が行われたかどうか"
          },
          "edit_type": {
            "type": "string",
            "enum": ["NONE", "NORMALIZE", "VTT_SUPPLEMENT", "UNRESOLVED"],
            "description": "修正種別。NONE=修正なし、NORMALIZE=表記揺れ・誤字修正のみ、VTT_SUPPLEMENT=VTT根拠ありの補完、UNRESOLVED=判定不能"
          },
          "edit_note": {
            "type": "string",
            "description": "修正内容の簡潔な説明。修正なしの場合は空文字"
          }
        },
        "required": ["id", "speaker", "text", "category", "uncertain", "uncertain_reason", "uncertain_span_ids", "source", "source_ids", "vtt_supplemented", "edit_type", "edit_note"]
      }
    }
  },
  "required": ["utterances"]
}
```

`chunk_summary` はデフォルトでは出力しない。`config.yaml` の `api.include_chunk_summary: true` を設定した場合のみ、スキーマに以下を追加する。

```json
"chunk_summary": {
  "type": "string",
  "description": "このチャンクの内容の簡潔な要約（デバッグ・診断用）"
}
```

### 7.4 source_ids の変換規則

`source_ids` はLLM出力の各utteranceが「どの主VTTキューから生成されたか」を追跡するためのフィールドである。以下の規則を厳守すること。

**許可される操作:**

| 操作 | 説明 | 例 |
|------|------|----|
| 1対1対応 | 1つの主VTTキューがそのまま1つのutteranceになる | `source_ids: ["U000010"]` |
| 連続結合 | 連続するIDを1つのutteranceにまとめる（短い発話の統合等） | `source_ids: ["U000010", "U000011", "U000012"]` |
| VTT補完挿入 | 主VTTに存在しない発話をVTT根拠で新規追加する | `id: "V_INSERT_001"`, `source_ids: []` |

**禁止される操作:**

| 操作 | 理由 |
|------|------|
| 非連続IDの結合 | ID対応表でのタイムスタンプ復元が不可能になる。例: `["U000010", "U000015"]` は禁止 |
| 1つのsource_idを複数utteranceに分割 | 同一IDが複数行に出現すると最終結合で重複が発生する。例: `U000010` が2つのutteranceに登場することは禁止 |
| 処理対象外IDの出力 | オーバーラップ区間（前文脈・後文脈）のIDを出力してはならない |

**バリデーション（Python側で実施）:**

- 出力された `source_ids` がすべて入力IDリスト（本体部分）に含まれることを確認する
- 同一IDが複数utteranceに出現していないことを確認する
- `source_ids` 内のIDが連続していることを確認する（`U000010, U000012` のような飛びを検出）
- 違反があった場合はリトライ対象とする（8.2節参照）

---

## 8. コアロジック（LLM指示ルール）

### 8.1 統合・補完ルール

| ルール | 説明 |
|--------|------|
| 主VTT優先 | 主VTTのテキスト・話者を基本とする |
| VTT補完条件 | 主VTTに該当区間の発話が欠落しており、かつVTTに対応テキストが存在する場合のみ |
| 捏造禁止 | 主VTT・Zoom VTTどちらにも根拠がない内容の追加・推測を一切禁止する |
| テキスト修正 | 明らかなASR誤変換の修正は許可する。`edit_type` を `NORMALIZE` とし、`edit_note` に修正内容を記録する |

### 8.2 相槌の分類と削除

#### 分類（LLMが実行）

| カテゴリ | 説明 | 例 |
|---------|------|------|
| `CONTENT` | 内容のある発話 | 「来週の会議は火曜日です」 |
| `BACKCHANNEL` | 相槌・フィラー | 「うん」「ええ」「あー」 |
| `ACK_DECISION` | 承認・合意・意思決定（削除禁止） | 「はい、それで進めましょう」「了解です」 |

#### 削除条件（TXT生成時のみ、Python側で判定）

`BACKCHANNEL` に分類された発話のうち、以下の**すべて**を満たすもののみTXTから除外する。

1. テキスト長が短い（目安: 10文字以下）
2. 数字・固有名詞・否定語・依頼表現・結論語を含まない
3. `ACK_DECISION` に分類されていない（= `BACKCHANNEL` であることが前提）

迷った場合は削除しない。主VTT/Zoom VTTには相槌もレビュー用として残す。

### 8.3 不確実箇所の扱い

LLMは不確実箇所を `uncertain: true` としてマークし、理由を `uncertain_reason` に記載する。

| 理由 | 説明 |
|------|------|
| `AB_MISMATCH` | 主VTTとZoom VTTの内容が不一致 |
| `LOW_CONFIDENCE` | 両方とも聞き取りにくい |
| `SPEAKER_AMBIGUOUS` | 話者の判定が困難 |
| `OVERLAP` | 発話が重複している |

`uncertain_span_ids` でIDの範囲を指定する。最終出力でPython側が `start/end` を復元し、TXT/SRT/VTTに以下の形式でアノテーションする。

```
（聞き取り不確実 00:12:34–00:12:41 / 理由: 主VTTとZoom VTTが不一致）
```

---

## 9. IDバリデーション（LLM出力の検証）

LLMに構造化JSON出力を強制しても、長文出力時に以下の問題が発生しうる。

- 一部のIDの出力をスキップする（ロスト）
- 存在しない連番IDを勝手に作り出す（幻覚）
- オーバーラップ区間のIDを誤って出力する

### 9.1 バリデーション手順

LLMからのJSON返却後、以下のチェックをPython側で実施する。

| チェック | 内容 | 不合格時の動作 |
|---------|------|---------------|
| ID網羅性 | 入力した本体部分の全IDが、出力のいずれかのutteranceの `source_ids` に含まれているか | リトライ |
| ID重複なし | 同一IDが複数utteranceの `source_ids` に出現していないか | リトライ |
| 未知ID排除 | 出力に含まれるIDがすべて入力IDリスト（本体 + V_INSERT_*）に属するか | リトライ |
| 連続性 | 各utteranceの `source_ids` 内のIDが連番で連続しているか | 警告（リトライはしない） |
| オーバーラップ漏出 | 前文脈・後文脈のIDが出力に含まれていないか | 該当utteranceを除去 |

### 9.2 リトライポリシー

IDバリデーション不合格によるリトライは、APIエラーのリトライ回数（最大3回）とは別枠で **最大2回** とする。合計で1チャンクあたり最大5回のAPI呼び出しが発生しうる。

2回のIDバリデーションリトライでも不合格の場合は、エラーとして `temp_chunk_XXX.error.json` に記録し、欠損チャンクとして扱う。

---

## 10. 話者ラベル仕様

- 出力の話者名は `SPEAKER_00` 等をそのまま使用する
- 実名化は後工程の置換を基本とする
- オプション: `speaker_map.json` を指定した場合、最終出力の全フォーマットで一括置換を行う

```json
{
  "SPEAKER_00": "田中",
  "SPEAKER_01": "鈴木"
}
```

---

## 11. 一時ファイル・レジューム仕様

### 11.1 ファイル構成

チャンクごとに以下のファイルを `temp/` ディレクトリに保存する。

| ファイル | 内容 |
|---------|------|
| `temp_chunk_001.json` | LLM出力（ID中心、タイムスタンプなし） |
| `temp_chunk_001.meta.json` | 処理メタ情報 |
| `temp_chunk_001.error.json` | エラー時のみ生成。原因・スタックトレース |

### 11.2 meta.json の構造

```json
{
  "chunk_index": 1,
  "status": "ok",
  "chunk_time_range": {"start_sec": 0.0, "end_sec": 312.5},
  "overlap_range": {
    "before": {"start_sec": null, "end_sec": null},
    "after": {"start_sec": 312.5, "end_sec": 327.5}
  },
  "input_counts": {"srt_cues": 48, "vtt_cues": 52},
  "id_validation": {"passed": true, "retries": 0},
  "api_provider": "google",
  "api_model": "gemini-2.0-flash",
  "retry_count": 0,
  "processing_time_sec": 8.3,
  "offset_applied_sec": 12.34,
  "timestamp": "2026-03-07T12:05:23Z"
}
```

### 11.3 レジューム動作

- `status: "ok"` のチャンクのみスキップ対象
- `status: "error"` または meta.json が存在しないチャンクは再処理対象
- 再実行時にチャンク分割が前回と異なる場合（入力ファイル変更など）、`--clean` フラグで `temp/` を初期化する

---

## 12. エラー処理

### 12.1 APIリトライ

| パラメータ | 値 |
|-----------|------|
| 最大リトライ回数 | 3 |
| バックオフ方式 | 指数バックオフ（2, 4, 8秒 + ジッター） |
| リトライ対象 | 5xx系、429（Rate Limit）、タイムアウト |
| リトライ非対象 | 4xx系（400 Bad Request等） |
| Rate Limit（429） | レスポンスヘッダーの `Retry-After` を尊重して待機 |

### 12.2 IDバリデーションリトライ

- APIエラーとは別枠で最大2回
- 詳細は第9章を参照

### 12.3 失敗時の動作

- `temp_chunk_XXX.error.json` を生成し、エラーコード・レスポンスボディ・スタックトレースを記録する
- 後続チャンクの処理は継続する（1チャンクの失敗で全体を止めない）

### 12.4 最終結合時の動作（完了モード）

| モード | 動作 |
|--------|------|
| `strict`（デフォルト） | 欠損チャンクが1つでもある場合は最終出力を生成せず停止する。欠損チャンク番号の一覧を表示する |
| `best_effort` | 欠損チャンクがある場合でも、欠損区間を明示したうえで仮の最終出力を生成する。欠損区間はTXTに `（★ チャンク3 処理失敗: 00:10:00–00:15:00）` のように挿入し、SRT/VTT/JSONでも該当区間をマークする |

`completion_mode` は config で指定する。

### 12.5 オフセット検出失敗時

- 警告ログを出力する
- LOW信頼度時の3分岐（3.2節 Phase 3参照）に従って動作する

### 12.6 JSONパースエラー

構造化JSON出力の強制により原則発生しないが、万一発生した場合はエラーログにrawレスポンスを保存し、同一チャンクをリトライする（最大リトライ回数に含む）。

---

## 13. 設定ファイル仕様

全パラメータを `config.yaml`（または `config.json`）で一元管理する。

```yaml
# === 入力 ===
input:
  primary_vtt_path: whisper_output.vtt
  zoom_vtt_path: zoom_output.vtt
  dictionary_path: null              # 専門用語辞書（任意）
  speaker_map_path: null             # 話者マップ（任意）

# === タイムオフセット ===
offset:
  mode: auto                         # auto | manual | skip
  manual_offset_sec: 0.0             # mode=manual のとき使用
  sample_windows: [head, mid, tail]  # サンプル窓の種別
  window_duration_sec: 300           # 各サンプル窓の長さ
  vtt_search_margin_sec: 300         # 中盤・終盤窓のVTT探索マージン
  similarity_threshold: 0.6          # テキスト類似度の閾値
  min_valid_pairs: 3                 # 最少有効ペア数
  mad_k: 3.0                         # MAD外れ値除去の倍率
  max_offset_sec: 600                # オフセット検出上限
  use_reading_normalization: false     # 初期実装では無効。本線安定後にtrueへ

# === チャンク分割 ===
chunking:
  target_duration_sec: 300
  search_window_stage1_sec: 180
  search_window_stage2_sec: 300
  gap_threshold_sec: 1.2
  overlap_sec: 15                    # 前後オーバーラップ（秒）

# === LLM API ===
api:
  provider: google                   # google | openai | anthropic
  model: gemini-2.0-flash            # プロバイダーに応じたモデル名
  max_retries: 3                     # APIエラーのリトライ上限
  max_validation_retries: 2          # IDバリデーションのリトライ上限
  backoff_base_sec: 2                # 指数バックオフの基底秒数
  timeout_sec: 120                   # リクエストタイムアウト
  rate_limit_respect: true           # 429時にRetry-Afterを尊重するか
  include_chunk_summary: false       # chunk_summaryを出力させるか（デフォルトOFF）

# === 出力 ===
output:
  directory: ./output
  temp_directory: ./temp
  formats: [txt, srt, vtt, json]
  completion_mode: strict            # strict | best_effort
  txt_mode: clean                    # clean（現行版）。将来: verbatim | readable

# === ログ ===
logging:
  log_level: INFO                    # DEBUG | INFO | WARNING | ERROR
  log_path: ./logs/merger.log        # ログファイルの出力先
  save_prompt: false                 # LLMに送信したプロンプトを保存するか
  save_raw_response: false           # LLMのrawレスポンスを保存するか
```

---

## 14. 実装ステップ（推奨開発順序）

### Step 1: パーサー・オフセット検出・ID付与・分割ロジック

- VTTパーサー（主VTT・Zoom VTT共通）の実装と単体テスト
- 日本語読み正規化（pykakasi）の組み込み
- 複数窓オフセット推定アルゴリズムの実装
- MADベース外れ値除去の実装
- ID付与ロジックの実装
- チャンク分割（オーバーラップ付き）の実装
- この段階での成果物: オフセットレポート + チャンク分割結果のダンプ

### Step 2: 単一チャンクの構造化JSON出力検証

- 1チャンクのみLLM APIに送信
- スキーマ通りのJSONが返ることを確認
- IDバリデーション（入力IDリスト vs 出力IDリスト）の動作確認
- 統合ルール（捏造禁止・VTT補完・相槌分類・edit_type）の品質を目視チェック

### Step 3: 全チャンク処理 + レジューム + 最終生成

- 全チャンクの順次処理とレジューム機能の実装
- オーバーラップ区間の除去ロジックの実装
- 中間ファイル保存・スキップ・結合の一連フローを確認
- TXT/SRT/VTT/JSON/offset_reportの最終生成
- strict / best_effort 両モードの動作確認

### Step 4: エンドツーエンドテスト

実際の会議データおよび合成データで通しテストを行う。

---

## 15. テスト仕様

### 15.1 オフセット検出テスト

| ケース | 入力条件 | 期待結果 |
|--------|---------|----------|
| オフセット0秒 | 主VTT/Zoom VTTが同時開始 | offset=0, HIGH信頼度 |
| オフセット+10秒 | VTTが10秒遅れて開始 | offset≈10.0, HIGH信頼度 |
| オフセット-5秒 | VTTが5秒早く開始 | offset≈-5.0, HIGH信頼度 |
| 大きなオフセット（+120秒） | VTTが2分遅れて開始 | offset≈120.0, HIGH or MEDIUM |
| 冒頭無音あり | 主VTT先頭30秒が無音 | 中盤・終盤窓で補完し、正しいoffsetを検出 |
| 表記ゆれ大 | 漢字/かな/カタカナが大きくブレる | 読み正規化により正しくマッチ |
| マッチ不能 | 主VTT/Zoom VTTの内容が完全に異なる | LOW信頼度、有効ペア0、offset=0 |

### 15.2 チャンク分割テスト

| ケース | 入力条件 | 期待結果 |
|--------|---------|----------|
| 標準分割 | 30分の会議 | 約6チャンク、各5分±3分 |
| 短い会議 | 3分の会議 | 1チャンク（分割なし） |
| 長時間会議 | 2時間の会議 | 約24チャンク、オーバーラップ正常 |
| ギャップなし | 連続発話のみ | 話者切替または文末で分割 |
| オーバーラップ検証 | 任意の2チャンク | 境界15秒が正しく重複 |

### 15.3 LLM処理テスト

| ケース | 入力条件 | 期待結果 |
|--------|---------|----------|
| ID全網羅 | 50ID入力 | 出力に50ID全てが出現 |
| ID幻覚 | LLMが存在しないIDを出力 | バリデーションで検出、リトライ |
| IDロスト | LLMが一部IDをスキップ | バリデーションで検出、リトライ |
| 相槌判定 | 「うん」「はい、それで」混在 | BACKCHANNEL / ACK_DECISION が正しく分類 |
| Zoom VTT補完 | 主VTTに欠落区間あり | V_INSERT_* で補完、source="ZOOM" |
| edit_type | 誤字あり/VTT補完あり/変更なし | NORMALIZE / VTT_SUPPLEMENT / NONE が正しく付与 |

### 15.4 レジューム・エラーテスト

| ケース | 入力条件 | 期待結果 |
|--------|---------|----------|
| 正常レジューム | 途中でプロセス停止 → 再実行 | 完了済みチャンクをスキップ |
| temp破損 | meta.jsonが壊れている | 該当チャンクを再処理 |
| 429発生 | Rate Limit超過 | Retry-After尊重後にリトライ成功 |
| 全チャンク成功（strict） | 全チャンクok | 最終出力4点セット生成 |
| 1チャンク欠損（strict） | チャンク3が失敗 | 停止、欠損番号表示 |
| 1チャンク欠損（best_effort） | チャンク3が失敗 | 欠損区間マーク付きで仮出力生成 |
| 同一入力で再実行 | 2回連続実行 | 出力が完全一致（冪等性） |

### 15.5 出力検証テスト

| ケース | 確認内容 |
|--------|---------|
| TXT形式 | 話者ラベル + 改行 + テキストの形式が正しいこと。BACKCHANNEL削除が条件通りであること |
| SRT形式 | 連番・タイムコード・話者付きテキストが標準SRT仕様に準拠すること |
| VTT形式 | `WEBVTT` ヘッダー・voice tag が正しいこと |
| JSON形式 | 全utteranceにstart/end/speaker/text/category等が揃っていること |
| 話者マップ | speaker_map.json適用時に全出力で一括置換されること |

---

## 付録A: 用語定義

| 用語 | 定義 |
|------|------|
| キュー (cue) | VTTの最小単位。タイムコード + テキストの1ブロック |
| チャンク (chunk) | LLM API 1回分の処理単位。複数キューをまとめたもの |
| オフセット (offset) | 2つの録音ソース間のタイムスタンプのズレ（秒） |
| オーバーラップ (overlap) | チャンク境界で隣接チャンクと重複させる区間。文脈保持に使用 |
| ID対応表 | `U000001` → `{start, end, speaker, text}` の辞書 |
| MAD | Median Absolute Deviation。中央値ベースの頑健な散らばり指標 |
| 構造化JSON出力 | LLM APIにスキーマを指定し、型付きJSONを返却させる機能の総称 |

## 付録B: ファイル一覧

```
project/
├── config.yaml
├── main.py
├── src/
│   ├── parser.py              # VTTパーサー（主VTT・Zoom VTT共通）
│   ├── normalizer.py          # テキスト正規化（読み変換含む）
│   ├── offset.py              # タイムオフセット推定（複数窓・MAD）
│   ├── chunker.py             # チャンク分割（オーバーラップ付き）
│   ├── id_manager.py          # ID付与・対応表管理
│   ├── llm_client.py          # LLM APIクライアント（プロバイダー抽象化）
│   ├── providers/
│   │   ├── google.py          # Gemini API実装
│   │   ├── openai.py          # OpenAI API実装
│   │   └── anthropic.py       # Anthropic API実装
│   ├── validator.py           # IDバリデーション
│   ├── resume.py              # レジューム管理
│   └── exporter.py            # 最終出力生成（TXT/SRT/VTT/JSON）
├── temp/                      # 中間ファイル
├── output/                    # 最終出力
│   ├── final_transcript.txt
│   ├── final_transcript.srt
│   ├── final_transcript.vtt
│   ├── final_transcript.json
│   └── offset_report.json
├── logs/
│   └── merger.log
└── tests/
    ├── test_parser.py
    ├── test_normalizer.py
    ├── test_offset.py
    ├── test_chunker.py
    ├── test_validator.py
    └── test_e2e.py
```

---

*--- End of Document ---*

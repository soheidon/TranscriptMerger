# Transcript-Merger-AI

Zoomクラウド録画VTTとWhisper+pyannote VTTの文字起こしを統合・整形するCLIツール。

> **⚠️ 機密データに関する注意**
>
> このツールは会議の文字起こしデータを扱います。以下のファイルを**絶対にGitにコミットしないでください**。
>
> - `jobs/*/input/` 内の実会議データ（VTTファイル）
> - `jobs/*/working/` 内の中間ファイル（temp、ログ、rawレスポンス）
> - `jobs/*/output/` 内の最終成果物
> - `.env` やAPIキーを含むファイル
>
> `.gitignore` で除外設定済みですが、`git add -f` や設定ミスに注意してください。
> 実ジョブデータはリポジトリ外に置くことを強く推奨します（後述）。

## セットアップ

```bash
# Python 3.10以上が必要
python --version

# 依存パッケージのインストール
pip install -r requirements.txt
```

APIキーを環境変数に設定します。

**cmd の場合:**
```cmd
set GEMINI_API_KEY=your-api-key-here
```

**PowerShell の場合:**
```powershell
$env:GEMINI_API_KEY = "your-api-key-here"
```

**永続化する場合（ユーザー環境変数に登録）:**
```powershell
[System.Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-api-key-here", "User")
```

## 使い方

### 1. ジョブフォルダを作る

会議ごとにフォルダを作り、`input/` にVTTファイルを置く。

```
2026-03-07_定例会議/
  input/
    whisper_output.vtt    ← Whisper+pyannoteの出力（VTT形式）
    zoom_output.vtt       ← Zoomクラウド録画の文字起こし
```

これだけでOK。`working/` と `output/` は自動で作られる。
フォルダ構造の見本は `jobs/example_job/` を参照してください。

### 2. 実行する

```bash
python main.py --job path/to/2026-03-07_定例会議
```

### 3. 成果物を確認する

```
2026-03-07_定例会議/
  output/
    final_transcript.txt     ← 読み物版（相槌除去済み）
    final_transcript.srt     ← レビュー・編集用
    final_transcript.vtt     ← Web互換
    final_transcript.json    ← 正本（メタ情報付き）
    offset_report.json       ← オフセット検出レポート
```

## 推奨フォルダ配置

リポジトリ本体（コード）と実ジョブデータ（会議の生データ）は**別の場所に置く**ことを推奨します。
これにより、実データを誤ってGitに巻き込むリスクを構造的に排除できます。

```
D:\dev\
  TranscriptMerger\               ← GitHubリポジトリ本体（コード・テスト・仕様書）

D:\TranscriptMergerJobs\          ← 実ジョブデータ（Git管理外）
  2026-03-07_定例会議\
    input\
    working\
    output\
    job.yaml
  2026-03-10_企画会議\
    input\
    working\
    output\
```

実行時はジョブフォルダのパスを `--job` で指定します:

```bash
cd D:\dev\TranscriptMerger
python main.py --job D:\TranscriptMergerJobs\2026-03-07_定例会議
```

リポジトリ内の `jobs/` フォルダでも動作しますが、実データを扱う場合は上記の分離配置が安全です。

## ジョブフォルダの構成

```
<会議名>/
├── input/                  ← ユーザーが用意する
│   ├── whisper_output.vtt
│   ├── zoom_output.vtt
│   ├── (dictionary.json)   ← 専門用語辞書（オプション）
│   └── (speaker_map.json)  ← 話者マップ（オプション）
├── working/                ← 自動生成。処理中のファイル
│   ├── temp/               ← チャンクごとの中間ファイル
│   └── logs/               ← 実行ログ
├── output/                 ← 自動生成。最終成果物
└── job.yaml                ← ジョブ固有設定（オプション）
```

## よくある操作

### 途中で止まった → 再実行（レジューム）

```bash
# そのまま再実行すれば、完了済みチャンクはスキップされる
python main.py --job path/to/会議フォルダ
```

### 最初からやり直したい

```bash
# working/ を初期化してから実行
python main.py --job path/to/会議フォルダ --clean
```

### オフセットを手動で指定したい

```bash
# VTTが12.5秒遅れている場合
python main.py --job path/to/会議フォルダ --offset-sec 12.5

# オフセット検出をスキップ（ズレなし扱い）
python main.py --job path/to/会議フォルダ --offset-skip
```

### 一部のチャンクが失敗しても出力がほしい

```bash
python main.py --job path/to/会議フォルダ --best-effort
```

### 会議ごとに設定を変えたい

ジョブフォルダに `job.yaml` を置く。`config.yaml` の値を上書きできる。

```yaml
# job.yaml の例
offset:
  mode: manual
  manual_offset_sec: 12.5

input:
  dictionary_path: dictionary.json
  speaker_map_path: speaker_map.json
```

## 設定の優先順位

```
CLI引数（最優先）
  ↓
job.yaml（ジョブ固有）
  ↓
config.yaml（全体既定）
  ↓
プログラム内蔵のデフォルト値
```

## 処理済みジョブのアーカイブ

出力が確定したジョブフォルダは、丸ごと別ドライブ等に移動・圧縮して保管できる。

```bash
# 例: アーカイブフォルダに移動
move D:\TranscriptMergerJobs\2026-03-07_定例会議 D:\TranscriptMerger_Archive\
```

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| オフセット検出がLOW信頼度 | `offset_report.json` の候補を確認し `--offset-sec` で手動指定 |
| 特定チャンクだけ失敗 | `working/temp/` の該当チャンクの `.error.json` を確認 |
| APIレートリミット | 自動でRetry-Afterを尊重してリトライする。頻発する場合は時間を空ける |
| 入力ファイルが見つからない | `input/` 内のファイル名を確認。デフォルトは `whisper_output.vtt` と `zoom_output.vtt` |

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照。

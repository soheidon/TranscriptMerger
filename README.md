# Transcript-Merger-AI

Zoomクラウド録画VTTとWhisper+pyannote VTTの文字起こしを統合・整形するCLIツール。

> **⚠️ 機密データに関する注意**
> このツールは会議の文字起こしデータを扱います。以下のファイルを**絶対にGitにコミットしないでください**。
> * `jobs/*/input/` 内の実会議データ（VTTファイル）
> * `jobs/*/working/` 内の中間ファイル（temp、ログ、rawレスポンス）
> * `jobs/*/output/` 内の最終成果物
> * `.env` やAPIキーを含むファイル
> 
> 
> `.gitignore` で除外設定済みですが、`git add -f` や設定ミスに注意してください。
> 実ジョブデータはリポジトリ外に置くことを強く推奨します（後述）。

## セットアップ

```bash
# Python 3.10以上が必要
python --version

# 仮想環境の作成と有効化
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 依存パッケージのインストール
pip install -r requirements.txt

```

APIキーを環境変数に設定します。

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

リポジトリ本体（コード）と実ジョブデータ（会議の生データ）は**別の場所に置く**ことを推奨します。これにより、実データを誤ってGitに巻き込むリスクを構造的に排除できます。

```text
C:\Users\user\dev\
  TranscriptMerger\               ← GitHubリポジトリ本体（コード・テスト・仕様書）
  TranscriptMergerJobs\           ← 実ジョブデータ（Git管理外）

    2026-03-07_定例会議\
      input\
      working\
      output\
      job.yaml

```

実行例:

```bash
cd C:\Users\user\dev\TranscriptMerger
python main.py --job C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議

```

## よくある操作

### 途中で止まった → 再実行（レジューム）

そのまま再実行すれば、完了済みチャンクはスキップされます。

### 最初からやり直したい

`working/` を初期化してから実行します。

```bash
python main.py --job path/to/会議フォルダ --clean

```

### オフセットの手動指定

VTTが遅れている場合などは秒数を直接指定できます。

```bash
python main.py --job path/to/会議フォルダ --offset-sec 12.5

```

## 処理済みジョブのアーカイブ

出力が確定したジョブフォルダは、丸ごと別ドライブ等に移動して保管することを推奨します。

```powershell
# 例: Cドライブの作業領域から、Dドライブのアーカイブフォルダへ移動する場合
move C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議 D:\TranscriptMerger_Archive\

```

## トラブルシューティング（Windows環境）

### GitHubへのPushがフリーズ・失敗する場合

Windows環境（特にConda併用時）ではSSH通信が不安定になることがあります。以下の手順を試してください。

1. **Conda環境をオフにする**: `conda deactivate` を実行し、左端の `(base)` 表示を消す。
2. **SSH切断バグの回避**: 以下のコマンドで設定を一時的に付与してPushする。
```powershell
$env:GIT_SSH_COMMAND="ssh -o IPQoS=none"
git push origin main

```



### APIレートリミット

自動でRetry-Afterを尊重してリトライしますが、頻発する場合は時間を空けて実行してください。

## ライセンス

MIT License. 詳細は [LICENSE](https://www.google.com/search?q=LICENSE) を参照。
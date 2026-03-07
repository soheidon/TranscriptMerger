# Transcript-Merger-AI

Zoomクラウド録画のVTTと、Whisper+pyannoteによる文字起こし結果を統合・整形するCLIツールである。

> **機密データに関する注意**
> このツールは会議の文字起こしデータを扱う。以下のファイルは**絶対にGitにコミットしてはならない**。
> - `TranscriptMergerJobs\...` 配下の実会議データ
> - 各ジョブの `input/` 内の入力ファイル
> - 各ジョブの `working/` 内の中間ファイル（temp、ログ、rawレスポンス等）
> - 各ジョブの `output/` 内の最終成果物
> - `.env` やAPIキーを含むファイル
>
> 実ジョブデータはリポジトリ外に置く運用を前提とする。  
> `.gitignore` は repo 内 `jobs/` に誤って実データを置いた場合の保険として設定してあるが、公開前には毎回 `git status` を確認すること。

## セットアップ

### 1. Python の確認

Python 3.10以上を使用する。

```powershell
python --version
```

### 2. 仮想環境の作成と有効化

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. 依存パッケージのインストール

```powershell
pip install -r requirements.txt
```

### 4. APIキーの設定

APIキーは環境変数 `GEMINI_API_KEY` で渡す。`config.yaml` に直接書かないこと。

**PowerShell で一時的に設定する場合**

```powershell
$env:GEMINI_API_KEY = "your-api-key-here"
```

**ユーザー環境変数として永続化する場合**

```powershell
[System.Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-api-key-here", "User")
```

## 推奨フォルダ配置

リポジトリ本体（コード）と実ジョブデータ（会議の生データ）は**別フォルダに置く**ことを推奨する。これにより、実データを誤ってGitに巻き込むリスクを構造的に下げることができる。

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

* `TranscriptMerger\` はGitHubにpushする対象である。
* `TranscriptMergerJobs\` は実データ置き場であり、Git管理しない。
* リポジトリ内の `jobs/example_job/` は**構造見本**であり、実データ置き場ではない。

## 使い方

### 1. ジョブフォルダを作成する

会議ごとにジョブフォルダを作成し、`input/` に入力ファイルを配置する。

```text
C:\Users\user\dev\TranscriptMergerJobs\
  2026-03-07_定例会議\
    input\
      whisper_output.vtt    ← Whisper+pyannoteの出力（VTT形式）
      zoom_output.vtt       ← Zoomクラウド録画の文字起こし
    job.yaml
```

`working/` と `output/` は必要に応じて自動生成される想定である。

### 2. 実行する

```powershell
cd C:\Users\user\dev\TranscriptMerger
python .\main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
```

### 3. 成果物を確認する

処理が完了すると、ジョブフォルダ内に成果物が出力される。

```text
2026-03-07_定例会議\
  output\
    final_transcript.txt     ← 読み物版（相槌除去済み）
    final_transcript.srt     ← レビュー・編集用
    final_transcript.vtt     ← Web互換
    final_transcript.json    ← 正本（メタ情報付き）
    offset_report.json       ← オフセット検出レポート
```

## よくある操作

### 途中で止まった場合の再実行（レジューム）

そのまま同じコマンドで再実行すれば、完了済みチャンクはスキップされる。

```powershell
python .\main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
```

### 最初からやり直したい場合

`working/` を初期化してから実行する。

```powershell
python .\main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --clean
```

### オフセットを手動指定したい場合

VTTが遅れている場合などは、秒数を直接指定できる。

```powershell
python .\main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --offset-sec 12.5
```

### 部分成功を許容したい場合

デフォルトは `strict` である。欠損チャンクがある場合でも可能な範囲で出力したい場合は `--best-effort` を使う。

```powershell
python .\main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --best-effort
```

## `jobs/example_job/` について

リポジトリ内の `jobs/example_job/` は、フォルダ構造の見本として置いてあるものである。

* 実運用では使用しない
* 実データは置かない
* `input/.gitkeep`、`working/.gitkeep`、`output/.gitkeep` は構造見本のためだけに存在する

実運用では、必ず `--job` でリポジトリ外の `TranscriptMergerJobs\...` を指定すること。

## 処理済みジョブのアーカイブ

出力が確定したジョブフォルダは、丸ごと別ドライブ等に移動して保管することを推奨する。

```powershell
move C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議 D:\TranscriptMerger_Archive\
```

## GitHub公開前の確認

公開前には最低限、以下を確認すること。

* `git status` で実データ・出力ファイル・APIキー関連ファイルが含まれていないこと
* `.env` ファイルが存在しないこと
* リポジトリ内 `jobs/` に `example_job/` 以外のジョブフォルダがないこと
* `config.yaml` にAPIキーが直接書かれていないこと
* テストが通ること

```powershell
git status
pytest tests/
```

## トラブルシューティング（Windows環境）

### GitHub への push がフリーズ・失敗する場合

Windows 環境では、特に Conda 併用時に SSH 通信が不安定になることがある。以下の手順を試す。

#### 1. Conda 環境をオフにする

```powershell
conda deactivate
```

左端の `(base)` 表示が消えればよい。

#### 2. GitHub の remote を SSH に設定する

```powershell
git remote set-url origin git@github.com:soheidon/TranscriptMerger.git
git remote -v
```

#### 3. 一時的に `IPQoS none` を付けて push する

```powershell
$env:GIT_SSH_COMMAND="ssh -o IPQoS=none"
git push -u origin main
```

#### 4. 恒久対策

毎回環境変数を設定したくない場合は、SSH 設定ファイルに GitHub 用の設定を書く。

```powershell
New-Item -ItemType Directory -Force -Path ~/.ssh
Add-Content -Path ~/.ssh/config -Value "Host github.com`n    IPQoS none"
```

### API レートリミットが出る場合

`Retry-After` を尊重して自動リトライする想定であるが、頻発する場合は時間を空けて再実行する。

## ライセンス

MIT License。詳細は `LICENSE` ファイルを参照。


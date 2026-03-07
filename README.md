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

Python 3.10以上を使用する。**推奨**: Python 3.13 系（`py -V:3.13` で起動する系統を正とする）。

```powershell
py -V:3.13 --version
```

### 2. 仮想環境の作成と有効化（オプション）

```powershell
py -V:3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. 依存パッケージのインストール

```powershell
py -V:3.13 -m pip install -r requirements.txt
```

> **補足**: Gemini SDK は `google-genai`（旧 `google-generativeai` の後継）を使用する。

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
      whisper_output.vtt       ← Whisper+pyannoteの出力（VTT形式）
      zoom_output.vtt          ← Zoomクラウド録画の文字起こし
      glossary.txt             ← 用語辞書（任意・1行1語）
      context_prompt.txt       ← LLM用の背景プロンプト（任意・後述）
      glossary_confirmed.tsv   ← 辞書前処理の出力（サブアプリで生成）
    job.yaml
```

`working/` と `output/` は必要に応じて自動生成される想定である。

### 2. メインアプリを実行する

```powershell
cd C:\Users\user\dev\TranscriptMerger
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
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
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
```

### 最初からやり直したい場合

`working/` を初期化してから実行する。

```powershell
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --clean
```

### オフセットを手動指定したい場合

VTTが遅れている場合などは、秒数を直接指定できる。

```powershell
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --offset-sec 12.5
```

### 部分成功を許容したい場合

デフォルトは `strict` である。欠損チャンクがある場合でも可能な範囲で出力したい場合は `--best-effort` を使う。

```powershell
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議" --best-effort
```

## 辞書（glossary）の使い方

固有名詞（人名・地名・施設名など）の表記ゆれや誤変換をLLMに正しく修正させるために、辞書を使うことができる。

### context_prompt.txt（LLM用の背景プロンプト）

`input/context_prompt.txt` には、**2つのVTTを突合してLLMに修正させる際に参照してほしい情報**を書く。  
メインアプリが各チャンク処理時にLLMに渡し、話者判定・表記統一・誤変換修正の判断材料になる。  
音声の背景を入力することで、修正精度をあげるために設けたオプションである。例示は下記。 

```text
この音声は、香川県綾歌郡宇多津町で実施したインタビュー調査の録音である。
インタビュー対象者は宇多津町の住民であり、地域の生活や地名・施設について話している。

宇多津町およびその周辺（丸亀市、坂出市、高松市など）の地名・施設名が会話中に出てくる可能性がある。
ただし、会話では正式名称ではなく、略称・通称・短い呼び方が使われることがある。

例：
- 「ユープラザうたづ」は会話では「ユープラザ」と呼ばれることがある
- 「プレイパークゴールドタワー」は「ゴールドタワー」と呼ばれることがある
- 「宇多津町役場」は「役場」と呼ばれることがある

地名・施設名らしき語が聞こえた場合は、文脈に照らして宇多津町周辺の名称である可能性を考慮してよい。
ただし、根拠が弱い場合に無理に正式名称へ当てはめてはならない。
確信が持てない場合は、不確実なまま保持し、必要に応じて不確実注記を付けること。
```

### 辞書ファイルの更新

`input/glossary.txt` に、**正しい表記**を1行1語で書く。
* `#` で始まる行はコメント（無視）
* 空行は無視
* 読みや誤変換例は書かなくてよい（LLMが推定する）

```text
宇多津
宇多津町役場
ユープラザうたづ
聖通寺山
```

### 辞書前処理サブアプリの実行

`glossary.txt` を編集したら、**辞書前処理**を実行して `glossary_confirmed.tsv` を生成する。  
本アプリ（メイン）は `glossary_confirmed.tsv` を参照する。

```powershell
cd C:\Users\user\dev\TranscriptMerger
py -V:3.13 tools/generate_glossary_tsv.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
```

* 入力: `input/glossary.txt`
* オプション: `input/context_prompt.txt` があれば読み込み、LLMに背景として渡す
* 出力: `input/glossary_confirmed.tsv`（表記と読みのTSV）
* 完了後、VS Code で TSV が自動で開く（`code` コマンドがある場合）

TSV の内容を確認し、必要なら手動で修正してからメインアプリを実行する。

### メインアプリの実行（辞書を使う場合の流れ）

1. `glossary.txt` を編集
2. 辞書前処理を実行 → `glossary_confirmed.tsv` を生成・確認
3. メインアプリを実行

```powershell
py -V:3.13 main.py --job "C:\Users\user\dev\TranscriptMergerJobs\2026-03-07_定例会議"
```

* `glossary_confirmed.tsv` が存在すれば自動で読み込まれる
* `glossary.txt` はあるが `glossary_confirmed.tsv` がない場合は警告が出る（辞書前処理を実行すること）

### 辞書を使わない場合

辞書ファイルを置かなければ、辞書なしでメインアプリが動作する。  
従来の `job.yaml` の `dictionary_path` で JSON 辞書を指定することも可能（フォールバック）。

---

## 実行環境に関する補足

今回から、実行環境は **Python 3.13 系** に切り替えている。
従来の 3.12 系は `Scripts` 周りの exe 更新で不安定だったため、現在は **`py -V:3.13` で起動する系統を正とする**。

### 現在の前提

* Python 本体: `C:\Users\Sohei\AppData\Local\Python\pythoncore-3.13-64\python.exe`
* 実行コマンド例:
  * `py -V:3.13 -m pip install -r requirements.txt`
  * `py -V:3.13 main.py --job <job_path>`
* Gemini SDK:
  * 旧 `google-generativeai` ではなく **`google-genai`** を使用
* Gemini モデル:
  * 現在は `gemini-3.1-pro-preview` で実行しているが、必要に応じて `gemini-2.5-flash` / `gemini-2.5-pro` に切り替え可能な設計

### 注意

* `python` コマンド自体はまだ別バージョンを向く可能性があるため、当面は **`py -V:3.13` を明示**して実行・検証すること
* Conda の `(base)` 自動起動は無効化済み
* PowerShell 起動時に Conda が前提にならない状態で検証している

### 改修時に気にしてほしいこと

* 3.13 環境でそのまま動くこと
* `google-genai` 前提で provider 実装・補助スクリプトが動くこと
* 依存関係や README のコマンド例も、必要なら `py -V:3.13` ベースに合わせること

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
py -V:3.13 -m pytest tests/
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


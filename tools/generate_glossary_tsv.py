"""
glossary.txt から glossary_confirmed.tsv を生成する補助スクリプト。

役割:
- ジョブフォルダ内 input/glossary.txt を読み取り
- input/context_prompt.txt があれば読み込み、LLM に背景情報として渡す
- 読みが分からない語は LLM の知識で調べ・推定させる
- LLM に投げて「表記\t読み」のTSV形式の候補を生成し
- input/glossary_confirmed.tsv に保存する
- 生成後、可能であれば VS Code で TSV を開く

使用例:
    python tools/generate_glossary_tsv.py --job jobs/2026-03-07_定例会議

前提:
- 環境変数 GEMINI_API_KEY に Gemini API キーが設定されていること
- google-genai パッケージがインストールされていること
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import List


def _build_prompt(terms: List[str], context_prompt: str | None = None) -> str:
    joined = "\n".join(f"- {t}" for t in terms)
    context_section = ""
    if context_prompt:
        context_section = f"""
【背景情報】（この会議/インタビューの文脈。通称・略称の判断や地名の読み推定に活用すること）
{context_prompt}

"""
    return f"""あなたは日本語話者向けの用語集作成アシスタントです。

ユーザーから、会議やインタビューに登場する可能性がある固有名詞の「正しい表記」だけが渡されます。
あなたの役割は、各用語について「表記」と「読み（ひらがな）」の対応表をTSV形式で出力することです。
{context_section}
重要なルール:
- 読みが分からない語があっても、あなたの知識（地名・施設名・固有名詞の読み）を活用して調べ・推定すること
- 地域の地名・施設名などは、背景情報や一般的な読み方を参考にして推定してよい
- 出力形式は必ず TSV のみとし、説明文やコメントは一切含めないこと
- 各行は「表記[TAB]読み」とすること（ヘッダ行は不要）
- 読みは日本語ひらがなで書くこと（カタカナやローマ字は使わない）
- 会話で自然に使われる通称・略称が明らかに存在する場合のみ、通称・略称も別行として追加してよい
  例: 「ユープラザうたづ」→「ユープラザうたづ」「ユープラザ」、背景情報に「ユープラザと呼ばれることがある」とあればそれを反映
- ただし一般名詞になりすぎる語（「公園」「山」「役場」など）を単独の通称として新たに追加してはならない
- 自信がない場合は通称・略称を無理に追加せず、正式表記のみを出力してよい
- 入力にない新しい用語を勝手に作成してはならない

入力された用語一覧:
{joined}

上記のすべての用語について、「表記[TAB]読み」のTSVを出力してください。余計な説明やコメントは書かないでください。"""


def _call_gemini(prompt: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "google-genai パッケージが見つかりません。"
            "pip install google-genai でインストールしてください。"
        ) from e

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 GEMINI_API_KEY が設定されていません。")

    client = genai.Client(api_key=api_key)
    model_name = os.environ.get("GLOSSARY_MODEL", "gemini-2.5-flash")
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.1),
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("LLM から空のレスポンスが返されました。")
    return text.strip()


def _open_in_vscode(path: Path) -> None:
    """生成したTSVを VS Code で開こうと試みる。失敗しても致命的ではない。"""
    try:
        subprocess.run(["code", str(path)], check=False)
    except Exception:
        # code コマンドがない環境では何もしない
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="glossary.txt から glossary_confirmed.tsv を生成する補助スクリプト",
    )
    parser.add_argument(
        "--job",
        type=str,
        required=True,
        help="ジョブフォルダのパス（例: jobs/2026-03-07_定例会議）",
    )
    args = parser.parse_args()

    job_dir = Path(args.job).resolve()
    input_dir = job_dir / "input"
    glossary_txt = input_dir / "glossary.txt"
    output_tsv = input_dir / "glossary_confirmed.tsv"

    if not glossary_txt.exists():
        raise FileNotFoundError(f"glossary.txt が見つかりません: {glossary_txt}")

    # glossary.txt から用語を読み込む
    terms: List[str] = []
    with open(glossary_txt, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            terms.append(line)

    if not terms:
        raise ValueError(f"glossary.txt に有効な用語が含まれていません: {glossary_txt}")

    # context_prompt.txt があれば読み込み（読み推定・通称判断の参考にする）
    context_prompt = None
    for ctx_name in ("context_prompt.txt", "context_prompt.md"):
        ctx_path = input_dir / ctx_name
        if ctx_path.exists():
            context_prompt = ctx_path.read_text(encoding="utf-8").strip()
            print(f"context_prompt を読み込みました: {ctx_path}")
            break

    prompt = _build_prompt(terms, context_prompt)
    tsv_text = _call_gemini(prompt)

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_tsv, "w", encoding="utf-8", newline="") as f:
        f.write(tsv_text)

    print(f"glossary_confirmed.tsv を生成しました: {output_tsv}")
    _open_in_vscode(output_tsv)


if __name__ == "__main__":
    main()


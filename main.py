"""
Transcript-Merger-AI: メインエントリポイント

Usage:
    python main.py --job <ジョブフォルダパス> [options]
"""

import argparse
import logging
import sys
from pathlib import Path

from src.config_loader import load_config
from src.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    """CLIの引数をパースする。"""
    parser = argparse.ArgumentParser(
        description="Transcript-Merger-AI: VTT文字起こし統合・整形ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python main.py --job jobs/2026-03-07_定例会議
  python main.py --job jobs/2026-03-07_定例会議 --offset-sec 12.5
  python main.py --job jobs/2026-03-07_定例会議 --clean --best-effort
  python main.py --job jobs/2026-03-07_定例会議 --chunk 12
  python main.py --job jobs/2026-03-07_定例会議 --chunk 12 --chunk 31
        """,
    )

    # 必須引数
    parser.add_argument(
        "--job",
        type=str,
        required=True,
        help="ジョブフォルダのパス（例: jobs/2026-03-07_定例会議）",
    )

    # オフセット関連
    offset_group = parser.add_mutually_exclusive_group()
    offset_group.add_argument(
        "--offset-sec",
        type=float,
        default=None,
        help="手動でオフセット値を指定（秒、負値可）",
    )
    offset_group.add_argument(
        "--offset-auto",
        action="store_true",
        default=True,
        help="オフセット自動検出を実行（デフォルト）",
    )
    offset_group.add_argument(
        "--offset-skip",
        action="store_true",
        help="オフセット検出をスキップ（offset=0扱い）",
    )

    # 実行モード
    parser.add_argument(
        "--clean",
        action="store_true",
        help="working/ を初期化してから実行",
    )
    parser.add_argument(
        "--best-effort",
        action="store_true",
        help="欠損チャンクがあっても仮出力を生成（completion_mode=best_effort）",
    )

    # 部分再処理
    parser.add_argument(
        "--chunk",
        type=int,
        action="append",
        default=None,
        help="再処理対象のチャンク番号。複数指定可（例: --chunk 12 --chunk 31）",
    )

    # デバッグ
    parser.add_argument(
        "--debug",
        action="store_true",
        help="デバッグログを有効化",
    )

    return parser.parse_args()


def setup_logging(log_level: str, log_path: Path) -> None:
    """ロギングを設定する。

    Args:
        log_level: ログレベル（DEBUG, INFO, WARNING, ERROR）
        log_path: ログファイルの出力先パス
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main() -> None:
    """メイン処理。"""
    args = parse_args()

    # ジョブフォルダの存在確認
    job_dir = Path(args.job).resolve()
    if not job_dir.exists():
        print(f"エラー: ジョブフォルダが見つかりません: {job_dir}", file=sys.stderr)
        sys.exit(1)

    # 部分再処理時の安全策
    if args.clean and args.chunk:
        print(
            "エラー: --clean と --chunk は併用できません。"
            "部分再処理では既存チャンク結果を残す必要があります。",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.chunk:
        invalid = [c for c in args.chunk if c < 0]
        if invalid:
            print(f"エラー: チャンク番号は0以上で指定してください: {invalid}", file=sys.stderr)
            sys.exit(1)

    # 設定の読み込みとマージ（CLI引数 > job.yaml > config.yaml > デフォルト）
    app_config_path = Path(__file__).parent / "config.yaml"
    config = load_config(
        app_config_path=app_config_path,
        job_dir=job_dir,
        cli_args=args,
    )

    # ログ設定
    log_dir = job_dir / "working" / "logs"
    log_level = "DEBUG" if args.debug else config["logging"]["log_level"]
    setup_logging(log_level, log_dir / "merger.log")

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Transcript-Merger-AI 開始")
    logger.info(f"ジョブフォルダ: {job_dir}")
    if args.chunk:
        logger.info("対象チャンク限定モード: %s", sorted(set(args.chunk)))
    logger.info("=" * 60)

    # パイプライン実行
    # --clean の責務: main.py がフラグを受け取り、pipeline に渡す。
    # 実際の初期化処理は pipeline.py 内で ResumeManager.clean() を呼ぶ。
    try:
        run_pipeline(
            config=config,
            job_dir=job_dir,
            clean=args.clean,
            selected_chunks=set(args.chunk) if args.chunk else None,
        )
        logger.info("処理が正常に完了しました")
    except Exception:
        logger.exception("処理中にエラーが発生しました")
        sys.exit(1)


if __name__ == "__main__":
    main()

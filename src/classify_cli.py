"""研究方法分类命令行入口。"""
import argparse
from pathlib import Path

from config.settings import LLM_CONFIG, METHOD_CLASSIFICATION_CONFIG, OUTPUT_DIR
from .method_classifier import MethodClassifier
from .paper_summarizer import ModelClient


def main():
    parser = argparse.ArgumentParser(description="为已生成的论文摘要分类研究方法")
    parser.add_argument("--data-dir", default=OUTPUT_DIR, help="摘要数据目录")
    parser.add_argument("--cache-file", default=None, help="分类缓存 JSON 文件")
    parser.add_argument("--batch-size", type=int, default=None, help="每批论文数量")
    parser.add_argument("--force", action="store_true", help="忽略缓存并重新分类")
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="只处理最新摘要文件，不回填历史摘要",
    )
    args = parser.parse_args()

    if not METHOD_CLASSIFICATION_CONFIG.get("enabled", True):
        print("研究方法分类已在配置中关闭")
        return

    classifier_config = dict(METHOD_CLASSIFICATION_CONFIG)
    if args.batch_size:
        classifier_config["batch_size"] = args.batch_size
    model = classifier_config.get("model") or LLM_CONFIG.get("model")
    client = ModelClient(LLM_CONFIG.get("api_key"), model)
    classifier = MethodClassifier(client, classifier_config)
    stats = classifier.classify_directory(
        Path(args.data_dir),
        cache_file=args.cache_file,
        force=args.force,
        backfill_existing=(
            classifier_config.get("backfill_existing", True)
            and not args.latest_only
        ),
    )
    print(
        "研究方法分类完成："
        f"扫描 {stats['files']} 个文件 / {stats['records']} 篇论文，"
        f"新增分类 {stats['classified']}，未判定 {stats['unclassified']}，"
        f"复用缓存 {stats['reused']}，更新文件 {stats['changed_files']}。"
    )


if __name__ == "__main__":
    main()

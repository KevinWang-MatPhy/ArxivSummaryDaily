import os
import sys
import argparse
import re
import time
from datetime import datetime
from pathlib import Path
from .arxiv_client import ArxivClient
from .paper_summarizer import PaperSummarizer
from config.settings import SEARCH_CONFIG, CATEGORIES, QUERY, LLM_CONFIG, OUTPUT_DIR, LAST_RUN_FILE


TRANSIENT_ARXIV_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_transient_arxiv_error(error):
    """判断异常链中是否包含可恢复的 arXiv 限流或服务端错误。"""
    current = error
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        message = str(current)
        status = getattr(current, 'status', None)
        if status is None:
            match = re.search(r'HTTP\s+(\d{3})', message, re.IGNORECASE)
            status = int(match.group(1)) if match else None

        is_arxiv_error = (
            type(current).__module__.split('.')[0] == 'arxiv'
            or 'arxiv.org' in message.lower()
        )
        if is_arxiv_error and status in TRANSIENT_ARXIV_STATUS_CODES:
            return True

        current = current.__cause__ or current.__context__
    return False


def _has_existing_summaries(output_dir):
    """仅在已恢复历史摘要时允许临时使用旧数据继续构建站点。"""
    return any(Path(output_dir).glob('summary_*.md'))


def main(argv=None, sleep_func=time.sleep):
    parser = argparse.ArgumentParser(description='ArXiv论文摘要生成工具')
    parser.add_argument('--query', type=str, default=QUERY, help='搜索关键词')
    parser.add_argument('--categories', nargs='+', default=CATEGORIES, help='arXiv分类')
    parser.add_argument('--max-results', type=int, default=SEARCH_CONFIG['max_total_results'], help='获取论文数量')
    parser.add_argument('--output-dir', type=str, default=OUTPUT_DIR, help='输出目录')
    
    args = parser.parse_args(argv)
    
    # 更新配置
    SEARCH_CONFIG['max_total_results'] = args.max_results
    
    # 初始化客户端
    arxiv_client = ArxivClient(SEARCH_CONFIG)
    paper_summarizer = PaperSummarizer(LLM_CONFIG['api_key'], LLM_CONFIG.get('model'))
    
    # 准备 last_run_file 路径
    last_run_file = os.path.join(args.output_dir, LAST_RUN_FILE)
    
    max_attempts = max(1, int(SEARCH_CONFIG.get('workflow_retry_attempts', 3)))
    retry_delay = max(0, int(SEARCH_CONFIG.get('workflow_retry_delay', 60)))
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"开始第 {attempt} 次运行...")
            papers = arxiv_client.search_papers(
                categories=args.categories,
                query=args.query,
                last_run_file=last_run_file
            )
            if not papers:
                arxiv_client.save_last_run_info(
                    last_run_file=last_run_file,
                    total_results=0,
                )
                print("未找到符合条件的论文")
                return

            # 生成摘要
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(args.output_dir, f"summary_{timestamp}.md")

            # 生成摘要并保存
            success = paper_summarizer.summarize_papers(papers, output_file)
            if success:
                print(f"摘要已成功生成并保存到: {output_file}")
            else:
                raise RuntimeError("摘要生成失败，停止此次运行。")

            # 只有在摘要成功生成后才提交各分类游标和去重状态。
            arxiv_client.save_last_run_info(
                last_run_file=last_run_file,
                total_results=len(papers),
            )
            print("摘要成功生成，已更新各 arXiv 分类的增量运行记录。")
            return
        except Exception as e:
            print(f"运行过程中发生错误: {e}")
            transient_arxiv_error = _is_transient_arxiv_error(e)
            if attempt < max_attempts:
                if transient_arxiv_error:
                    delay = retry_delay * (2 ** (attempt - 1))
                    print(
                        f"arXiv 服务暂时限流或不可用，{delay} 秒后进行第 "
                        f"{attempt + 1} 次尝试..."
                    )
                    sleep_func(delay)
                else:
                    print("准备重新从头开始运行...")
                continue

            if (
                transient_arxiv_error
                and SEARCH_CONFIG.get('allow_stale_on_transient_error', True)
                and _has_existing_summaries(args.output_dir)
            ):
                print(
                    "::warning::arXiv API 在重试后仍不可用；已保留历史摘要，"
                    "本次按无新增论文继续后续分类和站点生成。"
                )
                return
            print("已达到最大重试次数，退出且不更新任何内容。")
            sys.exit(1)

if __name__ == '__main__':
    main()

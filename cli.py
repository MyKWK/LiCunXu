"""命令行工具 - 提供各种管理操作"""

import argparse
import sys

from loguru import logger


def setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        "logs/pipeline.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="50 MB",
    )


def cmd_seed(args):
    """加载种子数据"""
    from graph.seed_loader import load_seed_data
    stats = load_seed_data(clear_first=args.clear)
    print(f"种子数据加载完成: {stats}")


def cmd_ingest(args):
    """运行数据摄入 Pipeline"""
    from ingestion.pipeline import pipeline
    pipeline.run(
        clear_db=args.clear,
        resume=not args.no_resume,
        max_chunks=args.max_chunks,
        start_from=args.start_from,
    )


def cmd_ingest_from_saved(args):
    """从已保存结果恢复"""
    from ingestion.pipeline import pipeline
    pipeline.run_from_saved()


def cmd_ask(args):
    """命令行问答"""
    from rag.engine import rag_engine
    result = rag_engine.answer(args.question)
    print(f"\n问题: {result['question']}")
    if result.get('cypher'):
        print(f"Cypher: {result['cypher']}")
    print(f"\n回答:\n{result['answer']}")


def cmd_stats(args):
    """图谱统计"""
    from graph.crud import graph_crud
    stats = graph_crud.get_graph_stats()
    print("图谱统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


def cmd_serve(args):
    """启动 Web 服务"""
    from main import main
    main()


def cmd_process_text(args):
    """只做文本预处理"""
    from ingestion.text_processor import process_raw_files
    chunks = process_raw_files()
    print(f"处理完成，共 {len(chunks)} 个文本块")


def cmd_search_person(args):
    """按名字搜索人物"""
    from graph.crud import graph_crud
    results = graph_crud.get_person_by_name(args.name)
    if results:
        for r in results:
            p = r.get("p", r)
            print(f"  名字: {p.get('original_name', '?')}")
            print(f"  别名: {p.get('aliases', [])}")
            print(f"  角色: {p.get('role', '?')}")
            print(f"  效力: {p.get('loyalty', [])}")
            print(f"  描述: {p.get('description', '')}")
            print()
    else:
        print(f"未找到名为 [{args.name}] 的人物")


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="五代知识图谱管理工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # seed
    p_seed = subparsers.add_parser("seed", help="加载种子数据到 Neo4j")
    p_seed.add_argument("--clear", action="store_true", help="先清空数据库")
    p_seed.set_defaults(func=cmd_seed)

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="运行完整数据摄入 Pipeline")
    p_ingest.add_argument("--clear", action="store_true", help="先清空数据库")
    p_ingest.add_argument("--no-resume", action="store_true", help="不从断点续传，从头开始")
    p_ingest.add_argument("--max-chunks", type=int, default=None, help="最多处理多少个块")
    p_ingest.add_argument("--start-from", type=int, default=0, help="从第几个块开始（0-based）")
    p_ingest.set_defaults(func=cmd_ingest)

    # ingest-saved
    p_saved = subparsers.add_parser("ingest-saved", help="从已保存的抽取结果恢复写入图谱")
    p_saved.set_defaults(func=cmd_ingest_from_saved)

    # ask
    p_ask = subparsers.add_parser("ask", help="命令行问答")
    p_ask.add_argument("question", type=str, help="问题")
    p_ask.set_defaults(func=cmd_ask)

    # stats
    p_stats = subparsers.add_parser("stats", help="查看图谱统计")
    p_stats.set_defaults(func=cmd_stats)

    # serve
    p_serve = subparsers.add_parser("serve", help="启动 Web 服务")
    p_serve.set_defaults(func=cmd_serve)

    # process
    p_process = subparsers.add_parser("process", help="仅做文本预处理")
    p_process.set_defaults(func=cmd_process_text)

    # search
    p_search = subparsers.add_parser("search", help="按名字搜索人物")
    p_search.add_argument("name", type=str, help="人物名字")
    p_search.set_defaults(func=cmd_search_person)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
    else:
        args.func(args)


if __name__ == "__main__":
    main()

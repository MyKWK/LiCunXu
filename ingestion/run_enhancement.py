#!/usr/bin/env python3
"""知识图谱增强运行脚本

使用 deepseek-v3.2 模型重新提取知识，补全和完善现有知识图谱。

使用方法:
    # 从断点续传（推荐，可随时中断再恢复）
    python -m ingestion.run_enhancement

    # 全新提取（先清库再重建）
    python -m ingestion.run_enhancement --clear

    # 测试模式（只处理前 5 个块）
    python -m ingestion.run_enhancement --test 5

    # 从第 100 个块开始
    python -m ingestion.run_enhancement --start 100
"""
import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    parser = argparse.ArgumentParser(
        description="使用 deepseek-v3.2 增强五代十国知识图谱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从断点续传（推荐）
  python -m ingestion.run_enhancement

  # 不清库，从头开始
  python -m ingestion.run_enhancement --no-resume

  # 全新提取（清库后重建）
  python -m ingestion.run_enhancement --clear

  # 测试模式，只处理前10个块
  python -m ingestion.run_enhancement --test 10

  # 从第50个块开始处理
  python -m ingestion.run_enhancement --start 50
        """
    )

    parser.add_argument(
        "--clear",
        action="store_true",
        default=False,
        help="清空数据库后重新提取（慎用！）"
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="不从断点续传，从头开始（但不清库）"
    )

    parser.add_argument(
        "--test",
        type=int,
        default=None,
        metavar="N",
        help="测试模式：只处理前 N 个块"
    )

    parser.add_argument(
        "--start",
        type=int,
        default=0,
        metavar="N",
        help="从第 N 个块开始处理（0-based）"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="deepseek-v3.2",
        help="Venus 平台模型名称（默认: deepseek-v3.2）"
    )

    args = parser.parse_args()

    # 打印配置
    print("=" * 60)
    print("五代十国知识图谱增强系统")
    print(f"模型: {args.model} (via Venus)")
    print(f"清库: {'是' if args.clear else '否'}")
    print(f"断点续传: {'否' if args.no_resume or args.clear else '是'}")
    if args.test:
        print(f"测试模式: 处理前 {args.test} 个块")
    if args.start > 0:
        print(f"起始位置: 第 {args.start} 个块")
    print("=" * 60)

    if args.clear:
        confirm = input("⚠️  确认清空数据库？此操作不可撤销！(输入 yes 确认): ")
        if confirm.strip().lower() != "yes":
            print("已取消")
            sys.exit(0)

    # 导入并运行 Pipeline
    from ingestion.enhanced_pipeline import EnhancedKnowledgePipeline

    pipeline = EnhancedKnowledgePipeline(model_name=args.model)
    pipeline.run(
        clear_db=args.clear,
        resume=not args.no_resume and not args.clear,
        max_chunks=args.test,
        start_from=args.start,
    )


if __name__ == "__main__":
    main()
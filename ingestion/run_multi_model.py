#!/usr/bin/env python3
"""多模型并行知识图谱增强运行脚本

使用多个 LLM 模型同时处理 chunk，突破单模型限流瓶颈。
默认使用 2 个主力模型：deepseek-v3.2, deepseek-v3.1-terminus
Fallback 模型：glm-5, minimax-m2.5（主力失败时自动切换）

使用方法:
    # 从断点续传（推荐，使用 2 个主力模型 + fallback）
    python -m ingestion.run_multi_model

    # 指定使用的模型
    python -m ingestion.run_multi_model --models deepseek-v3.2 deepseek-v3.1-terminus

    # 测试模式（只处理前 8 个块）
    python -m ingestion.run_multi_model --test 8

    # 全新提取（先清库再重建）
    python -m ingestion.run_multi_model --clear
"""
import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# 默认主力模型列表（并行处理 chunk）
DEFAULT_MODELS = [
    "deepseek-v3.2",
    "deepseek-v3.1-terminus",
]


def main():
    parser = argparse.ArgumentParser(
        description="多模型并行增强五代十国知识图谱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用全部 4 个模型，从断点续传
  python -m ingestion.run_multi_model

  # 只使用 2 个模型
  python -m ingestion.run_multi_model --models deepseek-v3.2 glm-5

  # 测试模式，只处理前 8 个块
  python -m ingestion.run_multi_model --test 8

  # 全新提取（清库后重建）
  python -m ingestion.run_multi_model --clear

  # 从第 100 个块开始
  python -m ingestion.run_multi_model --start 100
        """
    )

    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        metavar="MODEL",
        help=f"指定使用的主力模型（空格分隔），默认: {' '.join(DEFAULT_MODELS)}。Fallback 模型由 pipeline 自动管理。"
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

    args = parser.parse_args()

    models = args.models or DEFAULT_MODELS

    # 打印配置
    from ingestion.multi_model_pipeline import FALLBACK_MODELS
    print("=" * 60)
    print("五代十国知识图谱 —— 多模型并行增强系统")
    print(f"主力模型数: {len(models)}")
    for i, m in enumerate(models):
        print(f"  [主力-{i}] {m}")
    print(f"Fallback 模型:")
    for i, m in enumerate(FALLBACK_MODELS):
        print(f"  [备选-{i}] {m}")
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

    # 导入并运行多模型 Pipeline
    from ingestion.multi_model_pipeline import MultiModelPipeline

    pipeline = MultiModelPipeline(models=models)
    pipeline.run(
        clear_db=args.clear,
        resume=not args.no_resume and not args.clear,
        max_chunks=args.test,
        start_from=args.start,
    )


if __name__ == "__main__":
    main()

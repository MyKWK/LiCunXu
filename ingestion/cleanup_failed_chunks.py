#!/usr/bin/env python3
"""清理失败 chunk 的脚本

功能：
1. 从日志中提取所有失败的 chunk ID
2. 从进度文件中移除这些 chunk（让它们可以被重新处理）
3. 可选：立即重新运行 pipeline 来处理这些失败的 chunk

使用方法:
    # 只查看失败的 chunk（不修改任何文件）
    python -m ingestion.cleanup_failed_chunks --dry-run

    # 清理失败记录并重新处理
    python -m ingestion.cleanup_failed_chunks --reprocess

    # 只清理失败记录，不重新处理
    python -m ingestion.cleanup_failed_chunks
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


def extract_failed_chunks_from_log(log_file: Path) -> set[str]:
    """从日志中提取所有失败的 chunk ID
    
    失败的标志是 "fallback 也失败" 或 "所有模型（主力+fallback）均失败"
    """
    failed_chunks = set()
    
    if not log_file.exists():
        print(f"日志文件不存在: {log_file}")
        return failed_chunks
    
    # 匹配失败日志行中的 chunk_id
    # 格式: "块 chunk_XXXXX 提取失败" 或 "fallback 也失败" 前面的 chunk
    log_content = log_file.read_text(encoding="utf-8")
    
    # 方法1: 匹配 "块 chunk_XXXXX 提取失败"
    pattern1 = r"块 (chunk_\d+) 提取失败"
    matches1 = re.findall(pattern1, log_content)
    failed_chunks.update(matches1)
    
    # 方法2: 匹配 "fallback 也失败" 前面的 chunk（需要从上下文获取）
    # 日志格式: "── [Worker-X] [N/M] chunk_XXXXX (章节) ──" 后面跟着失败
    lines = log_content.split("\n")
    current_chunk = None
    for line in lines:
        # 更新当前处理的 chunk
        chunk_match = re.search(r"(chunk_\d+)", line)
        if chunk_match:
            current_chunk = chunk_match.group(1)
        
        # 检查是否失败
        if "fallback 也失败" in line or "所有模型（主力+fallback）均失败" in line:
            if current_chunk:
                failed_chunks.add(current_chunk)
    
    return failed_chunks


def get_unprocessed_chunks() -> set[str]:
    """获取所有未处理的 chunk ID"""
    chunks_file = settings.PROCESSED_DATA_DIR / "chunks.json"
    progress_file = settings.PROCESSED_DATA_DIR / "enhancement_progress.json"
    
    # 加载所有 chunk
    if not chunks_file.exists():
        print(f"chunks.json 不存在: {chunks_file}")
        return set()
    
    data = json.loads(chunks_file.read_text(encoding="utf-8"))
    all_chunks = set(c["chunk_id"] for c in data)
    
    # 加载已处理的 chunk
    processed = set()
    if progress_file.exists():
        progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
        processed = set(progress_data.get("processed_chunks", []))
    
    return all_chunks - processed


def remove_chunks_from_progress(chunks_to_remove: set[str], dry_run: bool = False) -> int:
    """从进度文件中移除指定的 chunk
    
    Returns:
        实际移除的数量
    """
    progress_file = settings.PROCESSED_DATA_DIR / "enhancement_progress.json"
    
    if not progress_file.exists():
        print(f"进度文件不存在: {progress_file}")
        return 0
    
    progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
    processed_chunks = set(progress_data.get("processed_chunks", []))
    
    # 计算交集（实际需要移除的）
    to_remove = processed_chunks & chunks_to_remove
    
    if not to_remove:
        print("没有需要移除的 chunk")
        return 0
    
    print(f"将从进度文件中移除 {len(to_remove)} 个 chunk:")
    for chunk_id in sorted(to_remove)[:20]:
        print(f"  - {chunk_id}")
    if len(to_remove) > 20:
        print(f"  ... 还有 {len(to_remove) - 20} 个")
    
    if dry_run:
        print("[DRY-RUN] 不会实际修改文件")
        return len(to_remove)
    
    # 移除并保存
    new_processed = sorted(processed_chunks - to_remove)
    progress_data["processed_chunks"] = new_processed
    progress_file.write_text(
        json.dumps(progress_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ 已更新进度文件，移除了 {len(to_remove)} 个 chunk")
    
    return len(to_remove)


def main():
    parser = argparse.ArgumentParser(
        description="清理失败的 chunk 并可选重新处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="只查看失败的 chunk，不修改任何文件"
    )

    parser.add_argument(
        "--reprocess",
        action="store_true",
        default=False,
        help="清理后立即重新运行 pipeline 处理失败的 chunk"
    )

    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="指定日志文件路径（默认使用最新的 multi_model_run.log）"
    )

    args = parser.parse_args()

    # 确定日志文件
    if args.log_file:
        log_file = Path(args.log_file)
    else:
        log_file = settings.PROJECT_ROOT / "logs" / "multi_model_run.log"

    print("=" * 60)
    print("五代十国知识图谱 —— 失败 Chunk 清理工具")
    print("=" * 60)

    # Step 1: 从日志中提取失败的 chunk
    print("\n[Step 1] 从日志中提取失败的 chunk...")
    failed_chunks = extract_failed_chunks_from_log(log_file)
    print(f"  从日志中找到 {len(failed_chunks)} 个失败的 chunk")

    # Step 2: 检查未处理的 chunk
    print("\n[Step 2] 检查未处理的 chunk...")
    unprocessed = get_unprocessed_chunks()
    print(f"  当前有 {len(unprocessed)} 个未处理的 chunk")

    # Step 3: 从进度文件中移除失败的 chunk
    print("\n[Step 3] 从进度文件中移除失败的 chunk...")
    removed = remove_chunks_from_progress(failed_chunks, dry_run=args.dry_run)

    # Step 4: 可选重新处理
    if args.reprocess and not args.dry_run and removed > 0:
        print("\n[Step 4] 重新运行 pipeline 处理失败的 chunk...")
        from ingestion.multi_model_pipeline import MultiModelPipeline
        
        pipeline = MultiModelPipeline()
        pipeline.run(
            clear_db=False,
            resume=True,
        )
    elif args.reprocess and args.dry_run:
        print("\n[DRY-RUN] 跳过重新处理步骤")
    else:
        print("\n✅ 清理完成！")
        print("   提示: 使用 --reprocess 参数可以立即重新处理这些 chunk")


if __name__ == "__main__":
    main()

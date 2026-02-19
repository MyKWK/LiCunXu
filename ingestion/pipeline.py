"""摄入 Pipeline - 增量式知识构建

核心改进：
1. 每提取一个 chunk 就立刻写入 Neo4j（不是攒到最后再写）
2. 人物写入时走 merge_person 做智能合并（基于名字/别名匹配）
3. 关系创建通过人名解析 uid（而非依赖 LLM 给出一致的 uid）
4. 支持断点续传（记录已处理的 chunk_id，跳过已处理的）
"""

import json
from pathlib import Path

from loguru import logger

from config.settings import settings
from graph.crud import graph_crud
from graph.schema import init_constraints
from ingestion.extractor import extractor
from ingestion.text_processor import process_raw_files, TextChunk
from models.entities import ExtractionResult


class IngestionPipeline:
    """知识摄入 Pipeline - 增量式构建

    流程：原始文本 -> 分块 -> LLM 抽取 -> 智能合并 -> 实时写入 Neo4j
    """

    def __init__(self):
        self._processed_chunks: set[str] = set()
        self._progress_file = settings.PROCESSED_DATA_DIR / "progress.json"

    def _load_progress(self):
        """加载已处理的 chunk_id 列表（支持断点续传）"""
        if self._progress_file.exists():
            try:
                data = json.loads(self._progress_file.read_text(encoding="utf-8"))
                self._processed_chunks = set(data.get("processed_chunks", []))
                logger.info(f"恢复进度: 已处理 {len(self._processed_chunks)} 个块")
            except Exception as e:
                logger.warning(f"进度文件加载失败: {e}")
                self._processed_chunks = set()

    def _save_progress(self):
        """保存当前进度"""
        self._progress_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"processed_chunks": sorted(self._processed_chunks)}
        self._progress_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_result_to_neo4j(self, result: ExtractionResult):
        """将单个抽取结果实时写入 Neo4j（带智能合并）

        这是核心：每处理完一个 chunk，立刻入库。
        下一个 chunk 的 LLM 提取就能看到新增的人物名单。
        """
        # 1. 写入政权
        for dynasty in result.dynasties:
            try:
                graph_crud.upsert_dynasty(dynasty)
            except Exception as e:
                logger.warning(f"政权写入失败 [{dynasty.name}]: {e}")

        # 2. 写入地点
        for place in result.places:
            try:
                graph_crud.upsert_place(place)
            except Exception as e:
                logger.warning(f"地点写入失败 [{place.name}]: {e}")

        # 3. 写入人物（核心：走 merge_person 智能合并）
        for person in result.persons:
            try:
                graph_crud.merge_person(person)
            except Exception as e:
                logger.warning(f"人物写入失败 [{person.original_name}]: {e}")

        # 4. 写入事件
        for event in result.events:
            try:
                graph_crud.upsert_event(event)
                # 关联参与者（通过名字查找）
                for participant_name in event.participants:
                    try:
                        graph_crud.link_event_participant(event.uid, participant_name)
                    except Exception as e:
                        logger.debug(f"事件参与关联失败: {e}")
            except Exception as e:
                logger.warning(f"事件写入失败 [{event.name}]: {e}")

        # 5. 写入关系（通过名字解析 uid）
        rel_ok = 0
        rel_fail = 0
        for relation in result.relations:
            try:
                success = graph_crud.create_relation_by_name(relation)
                if success:
                    rel_ok += 1
                else:
                    rel_fail += 1
            except Exception as e:
                logger.debug(f"关系创建异常: {e}")
                rel_fail += 1

        logger.info(
            f"  写入: {len(result.persons)}人物, {len(result.dynasties)}政权, "
            f"{len(result.events)}事件, {len(result.places)}地点, "
            f"{rel_ok}关系成功/{rel_fail}失败"
        )

    def run(
        self,
        clear_db: bool = False,
        resume: bool = True,
        max_chunks: int | None = None,
        start_from: int = 0,
    ):
        """运行完整的摄入流程

        Args:
            clear_db: 是否先清空数据库
            resume: 是否从上次断点继续
            max_chunks: 最多处理多少个块（用于测试，None=全部）
            start_from: 从第几个块开始（0-based）
        """
        logger.info("=" * 60)
        logger.info("开始知识摄入 Pipeline（增量模式）")
        logger.info("=" * 60)

        # Step 1: 初始化
        if clear_db:
            from graph.schema import clear_database
            clear_database()
            self._processed_chunks = set()
        elif resume:
            self._load_progress()

        init_constraints()

        # Step 2: 文本预处理（或加载已有块）
        chunks_file = settings.PROCESSED_DATA_DIR / "chunks.json"
        if chunks_file.exists():
            logger.info("[Step 2] 加载已有文本块...")
            data = json.loads(chunks_file.read_text(encoding="utf-8"))
            chunks = [TextChunk(**item) for item in data]
            logger.info(f"  加载了 {len(chunks)} 个文本块")
        else:
            logger.info("[Step 2] 处理原始文本...")
            chunks = process_raw_files()

        if not chunks:
            logger.warning("未找到文本块，退出")
            return

        # 过滤已处理的块
        if resume and self._processed_chunks:
            pending = [c for c in chunks if c.chunk_id not in self._processed_chunks]
            logger.info(f"  跳过已处理的 {len(chunks) - len(pending)} 个块，剩余 {len(pending)} 个")
            chunks = pending

        # 应用 start_from 和 max_chunks
        if start_from > 0:
            chunks = chunks[start_from:]
            logger.info(f"  从第 {start_from} 个块开始，剩余 {len(chunks)} 个")
        if max_chunks is not None:
            chunks = chunks[:max_chunks]
            logger.info(f"  限制处理 {max_chunks} 个块")

        total = len(chunks)
        logger.info(f"[Step 3] 开始 LLM 提取 + 实时写入（共 {total} 块）...")

        # Step 3: 逐块提取 + 实时写入
        results = []
        for i, chunk in enumerate(chunks):
            logger.info(f"── [{i + 1}/{total}] {chunk.chunk_id} ({chunk.chapter}) ──")

            result = extractor.extract_from_chunk(chunk)
            if result:
                results.append(result)
                # 立刻写入 Neo4j
                self._write_result_to_neo4j(result)
            else:
                logger.warning(f"  块 {chunk.chunk_id} 提取失败，跳过")

            # 记录进度
            self._processed_chunks.add(chunk.chunk_id)

            # 每 10 块保存一次进度和中间结果
            if (i + 1) % 10 == 0:
                self._save_progress()
                self._save_extraction_results(results, suffix=f"_checkpoint_{i + 1}")
                stats = graph_crud.get_graph_stats()
                logger.info(f"  [进度] 已处理 {i + 1}/{total}, 图谱: {stats}")

            # 控制请求频率
            import time
            time.sleep(0.3)

        # Step 4: 最终保存
        self._save_progress()
        self._save_extraction_results(results)

        # Step 5: 统计
        stats = graph_crud.get_graph_stats()
        logger.info("=" * 60)
        logger.info(f"[完成] 图谱统计: {stats}")
        logger.info("=" * 60)

    def _save_extraction_results(self, results: list[ExtractionResult], suffix: str = ""):
        """保存抽取结果"""
        filename = f"extraction_results{suffix}.json"
        output_path = settings.PROCESSED_DATA_DIR / filename
        data = [r.model_dump() for r in results]
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def run_from_saved(self):
        """从已保存的抽取结果恢复写入（跳过 LLM 步骤）"""
        results_path = settings.PROCESSED_DATA_DIR / "extraction_results.json"
        if not results_path.exists():
            logger.error(f"未找到抽取结果文件: {results_path}")
            return

        logger.info(f"从文件加载抽取结果: {results_path}")
        data = json.loads(results_path.read_text(encoding="utf-8"))
        results = [ExtractionResult(**item) for item in data]

        init_constraints()
        for i, result in enumerate(results):
            logger.info(f"写入结果 [{i + 1}/{len(results)}]...")
            self._write_result_to_neo4j(result)

        stats = graph_crud.get_graph_stats()
        logger.info(f"图谱统计: {stats}")


pipeline = IngestionPipeline()


if __name__ == "__main__":
    pipeline.run(clear_db=True)

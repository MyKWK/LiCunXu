"""增强版知识提取 Pipeline - 使用 deepseek-v3.2 模型

基于已有 pipeline.py 改造，核心改进：
1. 使用 deepseek-v3.2 模型（Venus平台，中文更强）
2. 增强提示词（更关注战役、关系的完整性）
3. 提取结果直接实时写入 Neo4j（复用现有 graph_crud）
4. 支持断点续传
5. 处理完成后可选运行一致性检查
6. **自适应并发**：正常时并发处理，被限流时自动降级单线程，冷却后恢复并发

流程：原始文本 -> 分块 -> deepseek-v3.2 增量提取 -> 智能合并 -> 实时写入 Neo4j
"""

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from config.llm_client import VenusRateLimitError
from config.settings import settings
from graph.crud import graph_crud
from graph.schema import init_constraints
from ingestion.incremental_extractor import IncrementalKnowledgeExtractor
from ingestion.text_processor import process_raw_files, TextChunk
from models.entities import ExtractionResult


# ━━━━━━━━━━━━━━━ 并发配置 ━━━━━━━━━━━━━━━
CONCURRENCY_MAX_WORKERS = 4          # 最大并发数
CONCURRENCY_COOLDOWN_CHUNKS = 20     # 被限流后，单线程处理多少个 chunk 再尝试恢复并发
RATE_LIMIT_BACKOFF_SECONDS = 30      # 被限流后的初始退避等待时间（秒）


def _is_rate_limit_error(exception: Exception) -> bool:
    """判断异常是否为限流错误（优先检测 VenusRateLimitError 类型）"""
    # 直接类型匹配（最快最准）
    if isinstance(exception, VenusRateLimitError):
        return True
    # 兜底：检查异常消息中的关键词
    err_msg = str(exception).lower()
    rate_keywords = [
        "rate limit", "too many requests", "quota exceeded",
        "throttl", "429", "503", "限流", "配额", "请求过于频繁",
    ]
    return any(kw in err_msg for kw in rate_keywords)


class EnhancedKnowledgePipeline:
    """增强版知识提取 Pipeline

    与原 IngestionPipeline 的区别：
    - 使用 deepseek-v3.2 模型（原版用 qwen3-vl）
    - 增强提示词更关注事件和关系的完整性
    - 每15块刷新一次人物缓存（原版每20块）
    - **自适应并发**：并发处理 chunk，被限流时自动降级为串行，冷却后恢复
    - 更详细的进度日志
    """

    def __init__(self, model_name: str = "deepseek-v3.2"):
        self._processed_chunks: set[str] = set()
        self._progress_file = settings.PROCESSED_DATA_DIR / "enhancement_progress.json"
        self._heartbeat_file = settings.PROCESSED_DATA_DIR / "pipeline_heartbeat.json"
        self._extractor = IncrementalKnowledgeExtractor(model_name=model_name)

        # ── 并发控制 ──
        self._lock = threading.Lock()                 # 保护共享状态
        self._neo4j_write_lock = threading.Lock()     # 保护 Neo4j 写入（避免合并冲突）
        self._is_rate_limited = False                  # 当前是否处于限流状态
        self._consecutive_rate_limits = 0              # 连续限流计数
        self._chunks_since_rate_limit = 0              # 限流后已单线程处理的 chunk 数
        self._total_rate_limits = 0                    # 累计限流次数
        self._current_workers = CONCURRENCY_MAX_WORKERS  # 当前并发数

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

    def _update_heartbeat(self, chunk_id: str = "", status: str = "processing"):
        """更新心跳文件，让仪表盘知道 pipeline 正在运行"""
        try:
            self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            heartbeat = {
                "timestamp": time.time(),
                "status": status,
                "current_chunk": chunk_id,
                "processed_count": len(self._processed_chunks),
                "workers": self._current_workers,
                "rate_limited": self._is_rate_limited,
                "total_rate_limits": self._total_rate_limits,
            }
            self._heartbeat_file.write_text(
                json.dumps(heartbeat, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass  # 心跳失败不影响主流程

    def _write_result_to_neo4j(self, result: ExtractionResult):
        """将单个抽取结果实时写入 Neo4j（带智能合并）

        使用锁保护，确保并发时合并逻辑不冲突。
        """
        with self._neo4j_write_lock:
            # 1. 写入政权/势力
            for dynasty in result.dynasties:
                try:
                    graph_crud.merge_dynasty(dynasty)
                except Exception as e:
                    logger.warning(f"势力写入失败 [{dynasty.name}]: {e}")

            # 2. 写入地点
            for place in result.places:
                try:
                    graph_crud.upsert_place(place)
                except Exception as e:
                    logger.warning(f"地点写入失败 [{place.name}]: {e}")

            # 2.5 写入官职
            for title in result.official_titles:
                try:
                    graph_crud.merge_official_title(title)
                except Exception as e:
                    logger.warning(f"官职写入失败 [{title.name}]: {e}")

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
                    for participant_name in event.participants:
                        try:
                            graph_crud.link_event_participant(event.uid, participant_name)
                        except Exception as e:
                            logger.debug(f"事件参与关联失败: {e}")
                except Exception as e:
                    logger.warning(f"事件写入失败 [{event.name}]: {e}")

            # 5. 写入关系
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
                f"{len(result.official_titles)}官职, "
                f"{rel_ok}关系成功/{rel_fail}失败"
            )

    def _process_single_chunk(
        self,
        chunk: TextChunk,
        index: int,
        total: int,
    ) -> tuple[TextChunk, ExtractionResult | None, Exception | None]:
        """处理单个 chunk（线程安全），返回 (chunk, result, error)"""
        logger.info(f"── [{index + 1}/{total}] {chunk.chunk_id} ({chunk.chapter}) ──")
        self._update_heartbeat(chunk.chunk_id, "processing")
        try:
            result = self._extractor.extract_from_chunk(chunk)
            return (chunk, result, None)
        except Exception as e:
            logger.error(f"块 {chunk.chunk_id} 提取异常: {e}")
            return (chunk, None, e)

    def _handle_rate_limit(self):
        """处理限流：标记状态，计算退避时间"""
        with self._lock:
            self._is_rate_limited = True
            self._consecutive_rate_limits += 1
            self._total_rate_limits += 1
            self._chunks_since_rate_limit = 0

        # 指数退避：30s, 60s, 120s ... 最大 300s
        backoff = min(
            RATE_LIMIT_BACKOFF_SECONDS * (2 ** (self._consecutive_rate_limits - 1)),
            300,
        )
        logger.warning(
            f"🚫 检测到限流！（第 {self._total_rate_limits} 次）"
            f"降级为单线程，退避等待 {backoff}s..."
        )
        time.sleep(backoff)

    def _should_try_concurrent(self) -> bool:
        """判断是否应该尝试恢复并发"""
        with self._lock:
            if not self._is_rate_limited:
                return True  # 没被限流，当然并发
            if self._chunks_since_rate_limit >= CONCURRENCY_COOLDOWN_CHUNKS:
                # 单线程处理了足够多的 chunk，尝试恢复并发
                logger.info(
                    f"🔄 已单线程处理 {self._chunks_since_rate_limit} 个块，"
                    f"尝试恢复并发（{self._current_workers} workers）..."
                )
                self._is_rate_limited = False
                self._chunks_since_rate_limit = 0
                return True
            return False

    def _adaptive_concurrent_process(
        self,
        chunks: list[TextChunk],
        results: list[ExtractionResult],
        start_global_index: int,
        total: int,
    ):
        """自适应并发处理 chunks

        策略：
        - 正常时：使用 ThreadPoolExecutor 并发 N 个 chunk
        - 被限流时：立即取消未完成的并发任务，切换单线程，等待退避
        - 单线程处理 CONCURRENCY_COOLDOWN_CHUNKS 个后：重新尝试并发
        - 并发数可动态调整（连续限流时逐步降低）

        Args:
            chunks: 待处理的 chunk 列表
            results: 结果收集列表（会被原地追加）
            start_global_index: 全局起始索引（用于日志显示进度）
            total: 全局总数（用于日志显示进度）
        """
        i = 0
        processed_count = start_global_index

        while i < len(chunks):
            if self._should_try_concurrent() and (len(chunks) - i) >= 2:
                # ━━━━━━━━ 并发模式 ━━━━━━━━
                batch_size = min(self._current_workers, len(chunks) - i)
                batch = chunks[i:i + batch_size]

                logger.info(
                    f"⚡ 并发模式: 提交 {batch_size} 个 chunk "
                    f"({processed_count + 1}~{processed_count + batch_size}/{total})"
                )

                rate_limited_in_batch = False
                batch_results: list[tuple[TextChunk, ExtractionResult | None, Exception | None]] = []

                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    future_to_chunk = {}
                    for j, chunk in enumerate(batch):
                        future = executor.submit(
                            self._process_single_chunk,
                            chunk,
                            processed_count + j,
                            total,
                        )
                        future_to_chunk[future] = chunk

                    for future in as_completed(future_to_chunk):
                        try:
                            chunk_obj, result, error = future.result()

                            # 检查是否被限流
                            if error and _is_rate_limit_error(error):
                                rate_limited_in_batch = True
                                logger.warning(f"⚠️ chunk {chunk_obj.chunk_id} 触发限流")
                            elif result:
                                batch_results.append((chunk_obj, result, None))
                            else:
                                batch_results.append((chunk_obj, None, error))

                        except Exception as e:
                            chunk_obj = future_to_chunk[future]
                            if _is_rate_limit_error(e):
                                rate_limited_in_batch = True
                                logger.warning(f"⚠️ chunk {chunk_obj.chunk_id} 触发限流: {e}")
                            else:
                                batch_results.append((chunk_obj, None, e))
                                logger.error(f"chunk {chunk_obj.chunk_id} 未知异常: {e}")

                # 处理本批次成功的结果
                for chunk_obj, result, error in batch_results:
                    if result:
                        results.append(result)
                        self._write_result_to_neo4j(result)
                        # 只有成功的块才标记为已处理（断点续传时失败块需要重新处理）
                        with self._lock:
                            self._processed_chunks.add(chunk_obj.chunk_id)
                    else:
                        logger.warning(f"  块 {chunk_obj.chunk_id} 提取失败，跳过")

                if rate_limited_in_batch:
                    # 被限流了：只推进成功处理的那些 chunk
                    successfully_processed = len(batch_results)
                    i += successfully_processed
                    processed_count += successfully_processed

                    # 动态降低并发数（最低为2）
                    if self._current_workers > 2:
                        self._current_workers = max(2, self._current_workers - 1)
                        logger.info(f"📉 并发数降至 {self._current_workers}")

                    self._handle_rate_limit()
                else:
                    # 全部成功
                    i += batch_size
                    processed_count += batch_size
                    # 连续成功，重置限流计数，并尝试恢复并发数
                    with self._lock:
                        self._consecutive_rate_limits = 0
                        if self._current_workers < CONCURRENCY_MAX_WORKERS:
                            self._current_workers = min(
                                self._current_workers + 1,
                                CONCURRENCY_MAX_WORKERS,
                            )
                            logger.info(f"📈 并发数升至 {self._current_workers}")

            else:
                # ━━━━━━━━ 单线程模式 ━━━━━━━━
                chunk = chunks[i]
                logger.info(
                    f"🐌 单线程模式 [{processed_count + 1}/{total}]: "
                    f"{chunk.chunk_id} ({chunk.chapter})"
                )

                result = self._extractor.extract_from_chunk(chunk)
                if result:
                    results.append(result)
                    self._write_result_to_neo4j(result)
                    # 只有成功的块才标记为已处理（断点续传时失败块需要重新处理）
                    with self._lock:
                        self._processed_chunks.add(chunk.chunk_id)
                else:
                    logger.warning(f"  块 {chunk.chunk_id} 提取失败，跳过")

                with self._lock:
                    self._chunks_since_rate_limit += 1

                i += 1
                processed_count += 1

                # 单线程模式下控制频率
                time.sleep(0.5)

            # ── 定期保存进度 & 刷新缓存 ──
            if processed_count > 0 and processed_count % 10 == 0:
                self._save_progress()
                self._save_extraction_results(results, suffix=f"_checkpoint_{processed_count}")
                self._extractor._build_existing_context(force_refresh=True)
                try:
                    stats = graph_crud.get_graph_stats()
                    logger.info(
                        f"  [进度] 已处理 {processed_count}/{total}, "
                        f"限流 {self._total_rate_limits} 次, "
                        f"当前并发数 {self._current_workers}, "
                        f"图谱: {stats}"
                    )
                except Exception:
                    pass

    def run(
        self,
        clear_db: bool = False,
        resume: bool = True,
        max_chunks: int | None = None,
        start_from: int = 0,
        max_workers: int | None = None,
    ):
        """运行增强版知识提取流程

        Args:
            clear_db: 是否先清空数据库
            resume: 是否从上次断点继续
            max_chunks: 最多处理多少个块（用于测试，None=全部）
            start_from: 从第几个块开始（0-based）
            max_workers: 最大并发数（默认使用 CONCURRENCY_MAX_WORKERS）
        """
        logger.info("=" * 60)
        logger.info("开始增强版知识提取 Pipeline（deepseek-v3.2 模型，自适应并发）")
        logger.info("=" * 60)

        # 应用自定义并发数
        if max_workers is not None:
            self._current_workers = min(max_workers, CONCURRENCY_MAX_WORKERS)
            logger.info(f"  并发数设为: {self._current_workers}")

        # Step 1: 初始化
        if clear_db:
            from graph.schema import clear_database
            clear_database()
            self._processed_chunks = set()
        elif resume:
            self._load_progress()

        init_constraints()

        # 写入种子官职（幂等，确保权威数据始终存在）
        try:
            graph_crud.seed_official_titles()
        except Exception as e:
            logger.warning(f"种子官职写入失败: {e}")

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
        logger.info(
            f"[Step 3] 开始 deepseek-v3.2 提取 + 实时写入（共 {total} 块，"
            f"初始并发数 {self._current_workers}）..."
        )

        # Step 3: 自适应并发提取 + 实时写入
        results = []
        start_time = time.time()

        self._adaptive_concurrent_process(chunks, results, 0, total)

        elapsed = time.time() - start_time
        avg_time = elapsed / len(results) if results else 0

        # Step 4: 最终保存
        self._save_progress()
        self._save_extraction_results(results)

        # Step 5: 统计
        try:
            stats = graph_crud.get_graph_stats()
            logger.info("=" * 60)
            logger.info(f"[完成] 图谱统计: {stats}")
            logger.info("=" * 60)
        except Exception as e:
            logger.warning(f"获取图谱统计失败: {e}")

        logger.info(
            f"共处理 {len(results)} 块成功, {total - len(results)} 块失败\n"
            f"总耗时: {elapsed:.1f}s ({elapsed/60:.1f}min), "
            f"平均: {avg_time:.1f}s/chunk\n"
            f"限流降级: {self._total_rate_limits} 次"
        )

        # 标记完成
        self._update_heartbeat("", "finished")

    def _save_extraction_results(self, results: list[ExtractionResult], suffix: str = ""):
        """保存抽取结果"""
        filename = f"enhancement_results{suffix}.json"
        output_path = settings.PROCESSED_DATA_DIR / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.model_dump() for r in results]
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# 全局实例
enhanced_pipeline = EnhancedKnowledgePipeline()


if __name__ == "__main__":
    # 默认从断点续传，不清库
    enhanced_pipeline.run(clear_db=False, resume=True)
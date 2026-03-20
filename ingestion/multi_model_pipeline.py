"""多模型并行知识提取 Pipeline

核心思想：
- 将待处理的 chunk 均分给 N 个不同的 LLM 模型
- 每个模型拥有独立的 IncrementalKnowledgeExtractor 实例
- 各模型在独立的线程中并行处理各自的 chunk 分片
- 共享同一个 Neo4j 写入锁和进度记录，避免冲突
- 每个模型还保留自己的 fallback 机制（单模型级别的容错）

优势：
1. 突破单模型限流瓶颈，N 个模型并行吞吐量接近 N 倍
2. 模型级别隔离，一个模型限流不影响其他模型继续工作
3. 兼容现有断点续传和进度保存机制
"""

import json
import math
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


# ━━━━━━━━━━━━━━━ 多模型并行配置 ━━━━━━━━━━━━━━━
# 主力模型列表（并行处理 chunk）
PRIMARY_MODELS = [
    "deepseek-v3.2",
    "deepseek-v3.1-terminus",
]

# 备选模型列表（当主力模型失败时 fallback 使用）
FALLBACK_MODELS = [
    "glm-5",
    "minimax-m2.5",
]

# 兼容旧引用
AVAILABLE_MODELS = PRIMARY_MODELS

# 每个模型的并发 worker 数
PER_MODEL_WORKERS = 1

# 限流后的退避时间（秒）
RATE_LIMIT_BACKOFF_SECONDS = 30

# 主力模型最大重试次数（超过后启用 fallback）
PRIMARY_MAX_RETRIES = 2


class ModelWorker:
    """单个模型的工作实例

    管理一个模型的提取器、分配到的 chunks、以及该模型的运行统计。
    """

    def __init__(self, model_name: str, worker_id: int, fallback_models: list[str] | None = None):
        self.model_name = model_name
        self.worker_id = worker_id
        self.extractor = IncrementalKnowledgeExtractor(model_name=model_name)
        self.chunks: list[TextChunk] = []

        # fallback 模型：懒初始化，只有需要时才创建 extractor
        self._fallback_models = fallback_models or []
        self._fallback_extractors: dict[str, IncrementalKnowledgeExtractor] = {}

        # 统计
        self.success_count = 0
        self.fail_count = 0
        self.fallback_success_count = 0  # fallback 模型挽救成功的次数
        self.rate_limit_count = 0
        self.total_time = 0.0

    def get_fallback_extractor(self, model_name: str) -> IncrementalKnowledgeExtractor:
        """懒初始化获取 fallback 模型的 extractor"""
        if model_name not in self._fallback_extractors:
            logger.info(f"[{self.label}] 初始化 fallback 模型: {model_name}")
            self._fallback_extractors[model_name] = IncrementalKnowledgeExtractor(model_name=model_name)
        return self._fallback_extractors[model_name]

    @property
    def label(self) -> str:
        return f"Worker-{self.worker_id}[{self.model_name}]"

    def __repr__(self):
        return f"<ModelWorker {self.label} chunks={len(self.chunks)}>"


class MultiModelPipeline:
    """多模型并行知识提取 Pipeline

    工作流程：
    1. 加载待处理 chunks（跳过已完成的）
    2. 将 chunks 均分给 N 个模型
    3. 每个模型在独立线程中串行处理自己的 chunks
    4. 所有模型共享 Neo4j 写入锁和进度文件
    5. 定期保存进度和刷新缓存

    架构示意：
    ┌──────────────────────────────────────┐
    │            MultiModelPipeline        │
    │                                      │
    │   ┌─────────┐  ┌─────────┐          │
    │   │ Worker-0 │  │ Worker-1 │  ...    │
    │   │ deepseek │  │ glm-5   │          │
    │   │ chunk 0  │  │ chunk 1  │          │
    │   │ chunk 4  │  │ chunk 5  │          │
    │   │ ...      │  │ ...      │          │
    │   └────┬─────┘  └────┬─────┘          │
    │        │             │                │
    │        ▼             ▼                │
    │   ┌──────────────────────────┐        │
    │   │ 共享 Neo4j 写入锁 & 进度 │        │
    │   └──────────────────────────┘        │
    └──────────────────────────────────────┘
    """

    def __init__(self, models: list[str] | None = None, per_model_workers: int = PER_MODEL_WORKERS):
        self._models = models or AVAILABLE_MODELS
        self._per_model_workers = per_model_workers

        # 共享状态（线程安全）
        self._lock = threading.Lock()
        self._neo4j_write_lock = threading.Lock()
        self._processed_chunks: set[str] = set()
        self._progress_file = settings.PROCESSED_DATA_DIR / "enhancement_progress.json"
        self._heartbeat_file = settings.PROCESSED_DATA_DIR / "pipeline_heartbeat.json"

        # 全局统计
        self._results: list[ExtractionResult] = []
        self._global_success = 0
        self._global_fail = 0
        self._global_total = 0

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
        with self._lock:
            self._progress_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"processed_chunks": sorted(self._processed_chunks)}
            self._progress_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _update_heartbeat(self, status: str = "processing", detail: str = ""):
        """更新心跳文件"""
        try:
            self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            heartbeat = {
                "timestamp": time.time(),
                "status": status,
                "mode": "multi_model",
                "models": self._models,
                "processed_count": len(self._processed_chunks),
                "detail": detail,
            }
            self._heartbeat_file.write_text(
                json.dumps(heartbeat, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _write_result_to_neo4j(self, result: ExtractionResult, worker_label: str):
        """将单个抽取结果实时写入 Neo4j（带写入锁保护）"""
        with self._neo4j_write_lock:
            rel_ok = 0
            rel_fail = 0

            # 1. 写入政权/势力
            for dynasty in result.dynasties:
                try:
                    graph_crud.merge_dynasty(dynasty)
                except Exception as e:
                    logger.warning(f"[{worker_label}] 势力写入失败 [{dynasty.name}]: {e}")

            # 2. 写入地点
            for place in result.places:
                try:
                    graph_crud.upsert_place(place)
                except Exception as e:
                    logger.warning(f"[{worker_label}] 地点写入失败 [{place.name}]: {e}")

            # 2.5 写入官职
            for title in result.official_titles:
                try:
                    graph_crud.merge_official_title(title)
                except Exception as e:
                    logger.warning(f"[{worker_label}] 官职写入失败 [{title.name}]: {e}")

            # 3. 写入人物
            for person in result.persons:
                try:
                    graph_crud.merge_person(person)
                except Exception as e:
                    logger.warning(f"[{worker_label}] 人物写入失败 [{person.original_name}]: {e}")

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
                    logger.warning(f"[{worker_label}] 事件写入失败 [{event.name}]: {e}")

            # 5. 写入关系
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
                f"  [{worker_label}] 写入: {len(result.persons)}人物, "
                f"{len(result.dynasties)}政权, {len(result.events)}事件, "
                f"{len(result.places)}地点, {len(result.official_titles)}官职, "
                f"{rel_ok}关系成功/{rel_fail}失败"
            )

    def _try_extract_with_model(
        self,
        extractor: IncrementalKnowledgeExtractor,
        chunk: TextChunk,
        model_label: str,
        max_retries: int = PRIMARY_MAX_RETRIES,
    ) -> ExtractionResult | None:
        """用指定模型/extractor 尝试提取，返回结果或 None

        处理限流重试逻辑，若最终失败返回 None。
        """
        for attempt in range(max_retries + 1):
            try:
                result = extractor.extract_from_chunk(chunk)
                return result  # 可能是 None（提取为空）或正常结果
            except VenusRateLimitError:
                if attempt < max_retries:
                    backoff = RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"[{model_label}] 块 {chunk.chunk_id} 限流 "
                        f"(重试 {attempt + 1}/{max_retries})，等待 {backoff}s..."
                    )
                    time.sleep(backoff)
                else:
                    logger.warning(f"[{model_label}] 块 {chunk.chunk_id} 限流重试耗尽")
                    return None
            except Exception as e:
                logger.error(f"[{model_label}] 块 {chunk.chunk_id} 异常: {e}")
                return None
        return None

    def _process_chunk_for_worker(
        self,
        worker: ModelWorker,
        chunk: TextChunk,
        chunk_global_index: int,
    ) -> bool:
        """使用指定 worker 处理单个 chunk，返回是否成功

        流程：
        1. 先用主力模型尝试（含限流重试）
        2. 主力模型失败 → 依次尝试 fallback 模型
        3. 所有模型都失败 → 记录失败
        """
        start_t = time.time()
        logger.info(
            f"── [{worker.label}] [{chunk_global_index + 1}/{self._global_total}] "
            f"{chunk.chunk_id} ({chunk.chapter}) ──"
        )

        # ── 第一步：用主力模型尝试 ──
        result = self._try_extract_with_model(
            worker.extractor, chunk, worker.label, max_retries=PRIMARY_MAX_RETRIES
        )

        if result is None:
            # 主力模型失败，记录限流次数
            worker.rate_limit_count += 1
            logger.warning(
                f"[{worker.label}] 主力模型失败，尝试 fallback 模型..."
            )

            # ── 第二步：依次尝试 fallback 模型 ──
            for fb_model in worker._fallback_models:
                fb_label = f"{worker.label}->fallback[{fb_model}]"
                logger.info(f"  🔄 [{fb_label}] 尝试 fallback...")
                try:
                    fb_extractor = worker.get_fallback_extractor(fb_model)
                    result = self._try_extract_with_model(
                        fb_extractor, chunk, fb_label, max_retries=1
                    )
                    if result:
                        logger.info(f"  ✅ [{fb_label}] fallback 成功！")
                        break
                    else:
                        logger.warning(f"  ❌ [{fb_label}] fallback 也失败")
                except Exception as e:
                    logger.error(f"  ❌ [{fb_label}] fallback 异常: {e}")
                    continue

        elapsed = time.time() - start_t

        # ── 记录结果 ──
        if result:
            # 写入 Neo4j
            self._write_result_to_neo4j(result, worker.label)

            # 更新共享状态
            with self._lock:
                self._processed_chunks.add(chunk.chunk_id)
                self._results.append(result)
                self._global_success += 1

            # 区分是主力成功还是 fallback 成功
            if elapsed > 0 and worker.rate_limit_count > 0:
                worker.fallback_success_count += 1
            worker.success_count += 1
            worker.total_time += elapsed
            return True
        else:
            logger.warning(
                f"[{worker.label}] 块 {chunk.chunk_id} 所有模型（主力+fallback）均失败"
            )
            with self._lock:
                self._processed_chunks.add(chunk.chunk_id)  # 标记为已处理（避免反复重试死循环）
                self._global_fail += 1
            worker.fail_count += 1
            return False

    def _run_worker(self, worker: ModelWorker):
        """运行单个模型 worker（在独立线程中执行）

        串行处理分配给该 worker 的所有 chunks。
        """
        logger.info(
            f"🚀 [{worker.label}] 启动！负责处理 {len(worker.chunks)} 个 chunk"
        )

        for i, chunk in enumerate(worker.chunks):
            # 跳过已处理的（可能其他 worker 处理过）
            with self._lock:
                if chunk.chunk_id in self._processed_chunks:
                    logger.debug(f"[{worker.label}] 跳过已处理的 {chunk.chunk_id}")
                    continue

            # 计算全局进度
            global_processed = len(self._processed_chunks)
            self._process_chunk_for_worker(worker, chunk, global_processed)

            # 控制请求频率（避免同一模型太密集）
            time.sleep(0.3)

            # 定期刷新知识缓存（每 10 个 chunk）
            if (i + 1) % 10 == 0:
                worker.extractor._build_existing_context(force_refresh=True)

            # 定期保存进度（每 5 个 chunk）
            if (i + 1) % 5 == 0:
                self._save_progress()

        logger.info(
            f"🏁 [{worker.label}] 完成！"
            f"成功 {worker.success_count}, 失败 {worker.fail_count}, "
            f"限流 {worker.rate_limit_count} 次, "
            f"平均耗时 {worker.total_time / max(worker.success_count, 1):.1f}s/chunk"
        )

    def _distribute_chunks(self, chunks: list[TextChunk], workers: list[ModelWorker]):
        """将 chunks 均匀分配给各个 worker（轮转分配）"""
        for i, chunk in enumerate(chunks):
            worker_idx = i % len(workers)
            workers[worker_idx].chunks.append(chunk)

        # 打印分配结果
        for w in workers:
            logger.info(f"  📦 {w.label}: 分配 {len(w.chunks)} 个 chunk")

    def run(
        self,
        clear_db: bool = False,
        resume: bool = True,
        max_chunks: int | None = None,
        start_from: int = 0,
        models: list[str] | None = None,
    ):
        """运行多模型并行知识提取

        Args:
            clear_db: 是否先清空数据库
            resume: 是否从上次断点继续
            max_chunks: 最多处理多少个块（用于测试）
            start_from: 从第几个块开始（0-based）
            models: 自定义模型列表（不传则使用默认配置）
        """
        active_models = models or self._models
        active_fallbacks = FALLBACK_MODELS
        logger.info("=" * 60)
        logger.info("🚀 多模型并行知识提取 Pipeline 启动")
        logger.info(f"   主力模型: {active_models}")
        logger.info(f"   Fallback 模型: {active_fallbacks}")
        logger.info(f"   每模型并发数: {self._per_model_workers}")
        logger.info("=" * 60)

        # Step 1: 初始化
        if clear_db:
            from graph.schema import clear_database
            clear_database()
            self._processed_chunks = set()
        elif resume:
            self._load_progress()

        init_constraints()

        try:
            graph_crud.seed_official_titles()
        except Exception as e:
            logger.warning(f"种子官职写入失败: {e}")

        # Step 2: 加载/处理文本块
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

        if start_from > 0:
            chunks = chunks[start_from:]
            logger.info(f"  从第 {start_from} 个块开始，剩余 {len(chunks)} 个")
        if max_chunks is not None:
            chunks = chunks[:max_chunks]
            logger.info(f"  限制处理 {max_chunks} 个块")

        self._global_total = len(chunks)
        if self._global_total == 0:
            logger.info("✅ 没有待处理的块，已全部完成！")
            return

        # Step 3: 创建 workers 并分配 chunks
        logger.info(f"[Step 3] 创建 {len(active_models)} 个模型 Worker，分配 {self._global_total} 个 chunk...")
        workers: list[ModelWorker] = []
        for i, model_name in enumerate(active_models):
            worker = ModelWorker(
                model_name=model_name,
                worker_id=i,
                fallback_models=active_fallbacks,
            )
            workers.append(worker)

        self._distribute_chunks(chunks, workers)

        # Step 4: 并行启动所有 workers
        logger.info(f"[Step 4] 启动 {len(workers)} 个 Worker 并行处理...")
        self._update_heartbeat("processing", f"{len(workers)} models parallel")
        start_time = time.time()

        # 进度监控线程
        stop_monitor = threading.Event()
        monitor_thread = threading.Thread(
            target=self._progress_monitor,
            args=(workers, start_time, stop_monitor),
            daemon=True,
        )
        monitor_thread.start()

        # 使用线程池并行运行所有 workers
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = {
                executor.submit(self._run_worker, w): w
                for w in workers
            }

            for future in as_completed(futures):
                worker = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"❌ {worker.label} 异常退出: {e}")

        stop_monitor.set()
        elapsed = time.time() - start_time

        # Step 5: 最终保存 & 统计
        self._save_progress()
        self._save_extraction_results(self._results)

        try:
            stats = graph_crud.get_graph_stats()
            logger.info("=" * 60)
            logger.info(f"[完成] 图谱统计: {stats}")
            logger.info("=" * 60)
        except Exception as e:
            logger.warning(f"获取图谱统计失败: {e}")

        # 打印各 worker 统计
        logger.info("\n📊 各模型 Worker 统计:")
        logger.info(f"{'Worker':<35} {'成功':>6} {'Fallback挽救':>12} {'失败':>6} {'限流':>6} {'平均耗时':>10}")
        logger.info("-" * 82)
        for w in workers:
            avg = w.total_time / max(w.success_count, 1)
            logger.info(
                f"{w.label:<35} {w.success_count:>6} {w.fallback_success_count:>12} "
                f"{w.fail_count:>6} {w.rate_limit_count:>6} {avg:>8.1f}s"
            )
        logger.info("-" * 82)

        total_success = sum(w.success_count for w in workers)
        total_fail = sum(w.fail_count for w in workers)
        total_rl = sum(w.rate_limit_count for w in workers)
        total_fb = sum(w.fallback_success_count for w in workers)
        logger.info(
            f"{'合计':<35} {total_success:>6} {total_fb:>12} {total_fail:>6} "
            f"{total_rl:>6} {elapsed/max(total_success,1):>8.1f}s"
        )
        logger.info(
            f"\n总耗时: {elapsed:.1f}s ({elapsed/60:.1f}min)\n"
            f"等效吞吐: {total_success / max(elapsed, 1) * 60:.1f} chunks/min"
        )

        self._update_heartbeat("finished", f"done in {elapsed/60:.1f}min")

    def _progress_monitor(
        self,
        workers: list[ModelWorker],
        start_time: float,
        stop_event: threading.Event,
    ):
        """进度监控线程：每 60 秒输出一次全局进度"""
        while not stop_event.is_set():
            stop_event.wait(60)
            if stop_event.is_set():
                break

            elapsed = time.time() - start_time
            processed = len(self._processed_chunks)
            remaining = self._global_total - processed
            speed = processed / max(elapsed, 1) * 60  # chunks/min

            if speed > 0:
                eta_min = remaining / speed
            else:
                eta_min = float('inf')

            worker_status = " | ".join(
                f"{w.label}: {w.success_count}✅/{w.fail_count}❌"
                for w in workers
            )

            logger.info(
                f"📈 [进度] {processed}/{self._global_total} "
                f"({processed/max(self._global_total,1)*100:.1f}%), "
                f"速度: {speed:.1f} chunks/min, "
                f"预计剩余: {eta_min:.0f}min\n"
                f"   {worker_status}"
            )

            self._save_progress()
            self._update_heartbeat(
                "processing",
                f"{processed}/{self._global_total}, {speed:.1f} chunks/min"
            )

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

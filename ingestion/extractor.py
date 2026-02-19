"""LLM 知识抽取器 - 改名感知版

核心改进：
1. 提示词强调"改名/赐名/赐姓"的识别和关联
2. 用人名（而非 uid）作为关系的 source/target
3. 要求 LLM 输出每个人物的所有已知名字变体
4. 向 LLM 提供当前图谱中已有的人物名单，帮助 LLM 做名字对齐
"""

import json
import re
import time

from loguru import logger

from config.llm_client import venus_llm
from ingestion.text_processor import TextChunk
from models.entities import ExtractionResult

# ━━━━━━━━━━━━━━━ 提示词 ━━━━━━━━━━━━━━━

EXTRACTION_SYSTEM_PROMPT = """你是一位五代十国历史专家和知识图谱工程师。你的任务是从五代历史文本中提取结构化的知识图谱数据。

## 核心背景
时间范围：907年（朱温篡唐）- 960年（赵匡胤陈桥兵变），聚焦中原五代（后梁、后唐、后晋、后汉、后周）。

## ⚠️ 最重要的注意事项：人物改名

五代时期人物改名**极其普遍**，你必须高度警惕：
- **赐姓改名**：被义父赐姓（如元行钦 → 被李存勖赐名"李绍荣"）
- **即位改名**：称帝后改名（如朱温 → 朱全忠 → 朱晃）
- **赐名**：被皇帝赐名（如安重诲原名可能不同）
- **胡汉名字**：沙陀人常有胡名和汉名（如李嗣源本名邈佶烈）
- **避讳改名**：为避讳而改名

**对于每一个人物，你必须：**
1. 列出他在文本中出现的**所有名字**（包括仅出现过一次的）
2. 在 aliases 字段中列出所有曾用名/别名/赐名
3. original_name 使用该人物**最常见、最广为人知**的名字

## 提取规则

### 1. 人物 (Person)
```json
{
  "uid": "person_最常用名拼音（全小写下划线连接）",
  "original_name": "最常用名字",
  "aliases": ["曾用名1", "赐名", "本名", ...],
  "role": "皇帝/将领/大臣/宦官/叛将/藩镇节度使/后妃/文人/僧侣/其他",
  "loyalty": ["效力过的势力1", "势力2"],
  "birth_year": null,
  "death_year": null,
  "death_cause": "死因（被杀/病死/自杀/战死等，如有）",
  "description": "简要描述此人的身份和事迹"
}
```

### 2. 政权 (Dynasty)
```json
{
  "uid": "dynasty_拼音",
  "name": "政权名",
  "founder": "建国者名字",
  "capital": "都城",
  "start_year": null,
  "end_year": null,
  "description": "简要描述"
}
```

### 3. 事件 (Event)
```json
{
  "uid": "event_简短拼音描述",
  "name": "事件名称",
  "event_type": "战争/政变/皇位更替/结盟/背叛事件/暗杀/叛乱/其他",
  "year": null,
  "location": "地点名",
  "participants": ["参与者名字1", "参与者名字2"],
  "outcome": "结果",
  "description": "详细描述"
}
```

### 4. 地点 (Place)
```json
{
  "uid": "place_拼音",
  "name": "古地名",
  "modern_name": "今地名",
  "description": "简要描述"
}
```

### 5. 关系 (Relation)
```json
{
  "source": "源人物名字（用 original_name）",
  "target": "目标人物名字（用 original_name）",
  "relation_type": "关系类型",
  "year": null,
  "description": "关系描述"
}
```

**关系类型**（用英文大写）：
- 亲族：FATHER_OF, MOTHER_OF, SIBLING, SPOUSE
- 核心（重点提取！）：ADOPTED_SON（义子）, BETRAYED（背叛）, KILLED（杀害）, REPLACED（篡位/取代）
- 政治军事：SERVED（效力）, COMMANDED（统帅）, ALLIED_WITH（结盟）, SUCCEEDED（继位）, SUBORDINATE（下属）, ADVISOR（谋臣）, RIVAL（对手）, SURRENDERED_TO（投降）

## 输出格式
只返回 JSON（不要有任何思考过程、解释文字或 markdown 标记），格式：
```json
{
  "persons": [...],
  "dynasties": [...],
  "events": [...],
  "places": [...],
  "relations": [...]
}
```
如果某类型没有可提取内容，返回空数组 []。"""


EXTRACTION_USER_TEMPLATE = """请从以下五代历史文本中提取知识图谱数据。

{existing_context}

【章节】{chapter}

【文本内容】
{content}

请严格按 JSON 格式返回。只返回 JSON，不要任何其他文字或思考过程。
注意：
1. 仔细识别文中所有人物及其名字变体，尤其是改名/赐名关系
2. 关系的 source 和 target 使用人物的 original_name（最常用名）
3. 即使一个人只出现了一次，也要提取出来"""


class KnowledgeExtractor:
    """基于 LLM 的知识抽取器 - 改名感知版"""

    def __init__(self):
        self._known_persons_cache: list[dict] | None = None
        self._cache_refresh_counter = 0

    def _build_existing_context(self, force_refresh: bool = False) -> str:
        """构建已有人物上下文，告诉 LLM 图谱中已有哪些人物

        这样 LLM 在遇到 "李绍荣" 时可以主动关联到已有的 "元行钦"。
        """
        if not force_refresh and self._known_persons_cache is not None and self._cache_refresh_counter < 20:
            self._cache_refresh_counter += 1
        else:
            try:
                from graph.crud import graph_crud
                self._known_persons_cache = graph_crud.get_all_person_names()
                self._cache_refresh_counter = 0
            except Exception:
                self._known_persons_cache = []

        if not self._known_persons_cache:
            return ""

        # 构建简洁的人物名单
        person_lines = []
        for p in self._known_persons_cache[:200]:  # 限制数量避免 token 超限
            name = p.get("name", "")
            aliases = p.get("aliases") or []
            if aliases:
                person_lines.append(f"- {name}（又名：{'、'.join(aliases)}）")
            else:
                person_lines.append(f"- {name}")

        context = (
            "【已入库人物名单（请将文本中的人物与以下已有人物对齐，避免重复创建。"
            "如果文本中出现的名字是某个已有人物的别名，请使用该已有人物的 original_name）】\n"
            + "\n".join(person_lines)
        )
        return context

    def extract_from_chunk(
        self,
        chunk: TextChunk,
        include_existing_context: bool = True,
    ) -> ExtractionResult | None:
        """从单个文本块中提取知识"""
        existing_context = ""
        if include_existing_context:
            existing_context = self._build_existing_context()

        user_prompt = EXTRACTION_USER_TEMPLATE.format(
            existing_context=existing_context,
            chapter=chunk.chapter,
            content=chunk.content,
        )

        try:
            messages = [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            raw_text = venus_llm.chat(messages, max_tokens=8192)

            json_data = self._parse_json_response(raw_text)
            if json_data is None:
                logger.warning(f"块 {chunk.chunk_id} JSON 解析失败，原文前200字: {raw_text[:200]}")
                return None

            result = ExtractionResult(
                source_text=chunk.content[:500],
                source_chapter=chunk.chapter,
                **json_data,
            )
            logger.info(
                f"块 {chunk.chunk_id}: 提取 {len(result.persons)} 人物, "
                f"{len(result.events)} 事件, {len(result.relations)} 关系"
            )
            return result

        except Exception as e:
            logger.error(f"块 {chunk.chunk_id} 提取失败: {e}")
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """从 LLM 回复中提取 JSON"""
        # 去除 <think>...</think> 思考标签（Qwen3 特有）
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找到最外层的 { }
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def extract_batch(
        self,
        chunks: list[TextChunk],
        on_result=None,
        save_interval: int = 10,
    ) -> list[ExtractionResult]:
        """批量提取，支持回调实时写入

        Args:
            chunks: 文本块列表
            on_result: 每提取一个结果后的回调函数 (result) -> None
            save_interval: 每处理多少块保存一次中间结果
        """
        results = []
        failed = 0
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            logger.info(f"正在提取 [{i + 1}/{total}]: {chunk.chunk_id} ({chunk.chapter})")
            result = self.extract_from_chunk(chunk)
            if result:
                results.append(result)
                # 实时回调 - 将结果立刻写入图谱
                if on_result:
                    try:
                        on_result(result)
                    except Exception as e:
                        logger.error(f"回调处理失败: {e}")
            else:
                failed += 1

            # 定期保存中间结果
            if save_interval > 0 and (i + 1) % save_interval == 0:
                self._save_intermediate(results, i + 1)
                # 每保存一次，强制刷新人物缓存
                self._build_existing_context(force_refresh=True)

            # 控制请求频率
            if i < total - 1:
                time.sleep(0.3)

        logger.info(f"批量提取完成: {len(results)}/{total} 成功, {failed} 失败")
        return results

    @staticmethod
    def _save_intermediate(results: list[ExtractionResult], processed_count: int):
        """保存中间结果"""
        from config.settings import settings

        output_path = settings.PROCESSED_DATA_DIR / f"extraction_intermediate_{processed_count}.json"
        data = [r.model_dump() for r in results]
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"中间结果已保存 ({processed_count} 块): {output_path}")


extractor = KnowledgeExtractor()

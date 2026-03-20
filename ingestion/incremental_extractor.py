"""增量知识提取器 - 基于现有 extractor.py 改造

核心改进：
1. 使用 Venus 平台的 deepseek-v3.2 模型（中文更强）
2. 实时从 Neo4j 获取已有知识做对比（增量提取）
3. 集成 AliasResolver 别名解析
4. 提取结果直接对接 ExtractionResult 模型 + graph_crud 写入
5. 增强的提示词，更关注战役、人物关系、事件完整性
"""

import json
import re
import time

from loguru import logger

from config.llm_client import VenusLLM
from config.settings import settings
from ingestion.text_processor import TextChunk
from models.entities import ExtractionResult


# ━━━━━━━━━━━━━━━ 增强版提示词 ━━━━━━━━━━━━━━━

ENHANCED_SYSTEM_PROMPT = """你是一位五代十国历史专家和知识图谱工程师。你的任务是从五代历史文本中**尽可能完整地**提取结构化知识图谱数据。

## 核心背景
时间范围：907年（朱温篡唐）- 960年（赵匡胤陈桥兵变），涵盖中原五代（后梁、后唐、后晋、后汉、后周）以及十国。

## ⚠️ 最重要的注意事项

### 1. 人物改名（五代最复杂的问题）
五代时期人物改名**极其普遍**，你必须高度警惕：
- **赐姓改名**：如元行钦 → 被李存勖赐名"李绍荣"
- **即位改名**：如朱温 → 朱全忠 → 朱晃
- **胡汉名字**：沙陀人常有胡名和汉名（如李嗣源本名邈佶烈）
- **避讳改名**：为避讳而改名
- **同音异字**：如"存勖"vs"存勗"

**对于每一个人物，你必须：**
1. 列出他在文本中出现的**所有名字**
2. 在 aliases 字段中列出所有曾用名/别名/赐名
3. original_name 使用该人物**最常见、最广为人知**的名字

### 2. 战役和事件（重点！必须尽量完整提取）
每一场战役、战争、政变、叛乱都非常重要，必须提取：
- **战役名称**（如果文中未明确命名，根据地点+时间自拟名称如"柏乡之战"）
- **时间**（年份，尽量精确到月）
- **地点**
- **参战双方/参与者**（所有提到的人物都列出）
- **结果**
- 即使是小规模冲突也要提取

### 3. 人物关系（重点！必须尽量完整提取）
每个人物之间的关系都要提取：
- 亲族关系（父子、兄弟、夫妻、义父子）
- 政治关系（君臣、同僚、对手）
- 军事关系（统帅-部将、敌对）
- 特殊事件关系（背叛、杀害、投降）

### 4. 质量要求
- 【准确第一】只提取文本中**明确提到的**信息，不要编造或推测
- 【宁缺勿错】对不确定的信息不提取，避免引入错误知识
- 【完整提取】但凡文中明确提到的人物、事件、关系，都不要遗漏

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
  "description": "简要描述此人的身份和事迹（尽量详细，包含主要战功、官职变迁、重要事件参与等）"
}
```

### 2. 政权/势力 (Dynasty) - 重要！包含藩镇！
不仅要提取正式政权，还要提取藩镇、割据势力、部族等：
- **政权**：后梁、后唐、后晋、后汉、后周、南唐、前蜀、吴越、契丹/辽、唐朝等
- **藩镇**：河东镇、宣武镇、魏博镇、成德镇、凤翔镇、卢龙镇、忠武镇、淄青镇、定难军、武平军等
- **部族**：沙陀族、契丹族等

```json
{
  "uid": "dynasty_拼音 或 faction_拼音",
  "name": "势力名称",
  "aliases": ["别名1", "别名2"],
  "faction_type": "政权/藩镇/割据势力/部族/其他",
  "founder": "建立者/首任节度使名字",
  "capital": "都城/治所",
  "start_year": null,
  "end_year": null,
  "predecessor": "前身势力名称（如后唐的前身是'河东镇'）",
  "description": "简要描述"
}
```

**藩镇提取示例**：
- 文中提到"李克用为河东节度使" → 提取藩镇"河东镇"，faction_type="藩镇"
- 文中提到"朱温据宣武军" → 提取藩镇"宣武镇"，faction_type="藩镇"
- 藩镇和政权的演化关系通过 predecessor 字段表达

**势力与人物的关系——一定要提取！**：
- 节度使→藩镇：用 SERVED 关系（source=人物, target=藩镇名）
- 建国者→政权：用 FOUNDED 关系
- 藩镇史→藩镇演变：记录在 predecessor 字段中

### 3. 事件 (Event) - 必须尽量完整提取！
```json
{
  "uid": "event_简短拼音描述",
  "name": "事件名称（必须有明确名称，如'柏乡之战'、'魏州兵变'等）",
  "event_type": "战争/政变/皇位更替/结盟/背叛事件/暗杀/叛乱/其他",
  "year": null,
  "location": "地点名",
  "participants": ["参与者名字1", "参与者名字2", ...],
  "outcome": "结果（谁胜谁败、带来什么后果）",
  "description": "详细描述（包含起因、经过、结果、影响）"
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

### 5. 官职 (OfficialTitle)
**重要**：提取的是通用官职名称，不含地名前缀。
例如文本中出现"河东节度使"、"河阳节度使"，提取的官职应该是"节度使"，而不是"河东节度使"。
同理，"马军都虞候"和"步军都虞候"是两个不同的官职，但"成德军马军都虞候"中应提取"马军都虞候"。

```json
{
  "uid": "title_拼音（全小写下划线连接）",
  "name": "官职名称（如'节度使'、'行军司马'、'马军都虞候'）",
  "aliases": ["官职简称或别名，如'节帅'"],
  "category": "军职/文职/中枢/地方/监察/藩镇/其他",
  "rank": "品级（如'从二品'，如不确定可为null）",
  "description": "官职职责简要描述"
}
```

### 6. 关系 (Relation) - 必须尽量完整提取！
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
- 事件相关：PARTICIPATED_IN_BATTLE（共同参战）, DEFEATED（击败）, BESIEGED（围攻）
- 人物与势力：SERVED（效力于藩镇/政权）, FOUNDED（建立政权）
- 人物与官职：HELD_POSITION（人物→官职，某人担任某官职）, APPOINTED_TO（人物→官职，被任命为某官职）
- 势力演化：EVOLVED_INTO（藩镇发展为政权，如河东镇→后唐）

## 输出格式
只返回 JSON（不要有任何思考过程、解释文字或 markdown 标记），格式：
```json
{
  "persons": [...],
  "dynasties": [...],
  "events": [...],
  "places": [...],
  "official_titles": [...],
  "relations": [...]
}
```
如果某类型没有可提取内容，返回空数组 []。

## 特别强调
1. **事件是最重要的！**一段文本中通常会提到多个事件，全部提取出来
2. **关系要详尽！**文本中隐含的关系也要提取（如"A率B攻C"隐含A→B的COMMANDED关系、A与C的RIVAL关系）
3. **人物要全面！**即使一个人只被提到一次名字也要提取
4. **藩镇/势力必须提取！**文中提到的藩镇（如XX镇、XX节度使、XX军）都要作为Dynasty节点提取，并建立人物与藩镇的SERVED关系
5. **官职必须提取！**文中出现的官职名称（如节度使、刺史、行军司马、都虞候等）都要作为OfficialTitle节点提取，并用HELD_POSITION关系将人物与官职关联"""


ENHANCED_USER_TEMPLATE = """请从以下五代历史文本中提取知识图谱数据。

{existing_context}

【章节】{chapter}

【文本内容】
{content}

请严格按 JSON 格式返回。只返回 JSON，不要任何其他文字或思考过程。
注意：
1. 仔细识别文中所有人物及其名字变体，尤其是改名/赐名关系
2. 关系的 source 和 target 使用人物的 original_name（最常用名）
3. **每一场战役、每一个事件都要提取，不要遗漏**
4. **每两个有互动的人物之间的关系都要提取**
5. 即使一个人只出现了一次，也要提取出来"""


class IncrementalKnowledgeExtractor:
    """增量知识提取器 - 使用 deepseek-v3.2 模型

    基于已有的 KnowledgeExtractor 改造，核心改进：
    1. 使用 deepseek-v3.2 模型（中文更强）
    2. 更强的提示词（更关注事件和关系的完整性）
    3. 从 Neo4j 实时获取已有人物做对齐
    4. 输出兼容现有 ExtractionResult 模型
    """

    def __init__(self, model_name: str = "deepseek-v3.2"):
        self._venus_llm = VenusLLM()
        # 覆盖模型名（同时更新 fallback 状态中的主模型记录）
        self._venus_llm.model = model_name
        self._venus_llm._primary_model = model_name
        self._venus_llm._active_model = model_name
        self._known_persons_cache: list[dict] | None = None
        self._known_dynasties_cache: list[dict] | None = None
        self._known_titles_cache: list[dict] | None = None
        self._cache_refresh_counter = 0

    def _build_existing_context(self, force_refresh: bool = False) -> str:
        """构建已有人物、势力和官职上下文，告诉 LLM 图谱中已有哪些实体"""
        if not force_refresh and self._known_persons_cache is not None and self._cache_refresh_counter < 15:
            self._cache_refresh_counter += 1
        else:
            try:
                from graph.crud import graph_crud
                self._known_persons_cache = graph_crud.get_all_person_names()
                self._known_dynasties_cache = graph_crud.get_all_dynasty_names()
                self._known_titles_cache = graph_crud.get_all_title_names()
                self._cache_refresh_counter = 0
            except Exception:
                self._known_persons_cache = []
                self._known_dynasties_cache = []
                self._known_titles_cache = []

        parts = []

        # 人物上下文
        if self._known_persons_cache:
            person_lines = []
            for p in self._known_persons_cache[:300]:
                name = p.get("name", "")
                aliases = p.get("aliases") or []
                if aliases:
                    person_lines.append(f"- {name}（又名：{'、'.join(aliases[:5])}）")
                else:
                    person_lines.append(f"- {name}")

            parts.append(
                "【已入库人物名单（请将文本中的人物与以下已有人物对齐，避免重复创建。"
                "如果文本中出现的名字是某个已有人物的别名，请使用该已有人物的 original_name。"
                "对于已有人物，重点提取其新的关系和参与的新事件）】\n"
                + "\n".join(person_lines)
            )

        # 势力/藩镇上下文
        if self._known_dynasties_cache:
            dynasty_lines = []
            for d in self._known_dynasties_cache[:100]:
                name = d.get("name", "")
                aliases = d.get("aliases") or []
                ft = d.get("faction_type") or "政权"
                suffix = f"[{ft}]"
                if aliases:
                    suffix += f"（又名：{'、'.join(aliases[:3])}）"
                dynasty_lines.append(f"- {name} {suffix}")

            parts.append(
                "\n【已入库政权/势力名单（文本中出现的藩镇、政权请与以下已有势力对齐，"
                "避免重复创建。如果出现新的藩镇/势力，请作为 Dynasty 节点提取，"
                "faction_type 设为'藩镇'或'政权'等）】\n"
                + "\n".join(dynasty_lines)
            )

        # 官职上下文
        if self._known_titles_cache:
            seed_lines = []
            llm_lines = []
            for t in self._known_titles_cache[:100]:
                name = t.get("name", "")
                category = t.get("category") or "其他"
                aliases = t.get("aliases") or []
                source = t.get("source") or "llm"
                suffix = f"[{category}]"
                if aliases:
                    suffix += f"（又名：{'、'.join(aliases[:3])}）"
                line = f"- {name} {suffix}"
                if source == "seed":
                    seed_lines.append(line)
                else:
                    llm_lines.append(line)

            title_context = (
                "\n【已入库官职名单】\n"
                "注意：提取通用官职名，去掉地名前缀，如'河东节度使'提取为'节度使'。\n"
            )
            if seed_lines:
                title_context += (
                    "\n★ 权威官职（史学家总结，不可修改，只能追加别名）：\n"
                    + "\n".join(seed_lines)
                )
            if llm_lines:
                title_context += (
                    "\n○ 已提取官职（可合并更新）：\n"
                    + "\n".join(llm_lines)
                )

            parts.append(title_context)

        return "\n".join(parts)

    def extract_from_chunk(
        self,
        chunk: TextChunk,
        include_existing_context: bool = True,
    ) -> ExtractionResult | None:
        """从单个文本块中提取知识"""
        existing_context = ""
        if include_existing_context:
            existing_context = self._build_existing_context()

        user_prompt = ENHANCED_USER_TEMPLATE.format(
            existing_context=existing_context,
            chapter=chunk.chapter,
            content=chunk.content,
        )

        try:
            messages = [
                {"role": "system", "content": ENHANCED_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            raw_text = self._venus_llm.chat(
                messages, 
                temperature=0.15,  # 更低温度确保准确性
                max_tokens=8192
            )

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
                f"{len(result.events)} 事件, {len(result.official_titles)} 官职, "
                f"{len(result.relations)} 关系"
            )
            return result

        except Exception as e:
            logger.error(f"块 {chunk.chunk_id} 提取失败: {e}")
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """从 LLM 回复中提取 JSON"""
        # 去除 <think>...</think> 思考标签
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
                if on_result:
                    try:
                        on_result(result)
                    except Exception as e:
                        logger.error(f"回调处理失败: {e}")
            else:
                failed += 1

            # 定期保存中间结果 + 刷新人物缓存
            if save_interval > 0 and (i + 1) % save_interval == 0:
                self._save_intermediate(results, i + 1)
                self._build_existing_context(force_refresh=True)

            # 控制请求频率
            if i < total - 1:
                time.sleep(0.5)

        logger.info(f"批量提取完成: {len(results)}/{total} 成功, {failed} 失败")
        return results

    @staticmethod
    def _save_intermediate(results: list[ExtractionResult], processed_count: int):
        """保存中间结果"""
        output_path = settings.PROCESSED_DATA_DIR / f"enhancement_intermediate_{processed_count}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.model_dump() for r in results]
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"中间结果已保存 ({processed_count} 块): {output_path}")
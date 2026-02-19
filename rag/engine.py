"""Graph RAG 问答引擎

将 Neo4j 知识图谱与 DeepSeek LLM 结合，实现基于图谱的智能问答。
Cypher 生成仍使用维纳斯 LLM（内部），回答生成使用 DeepSeek。
"""

import json
import re

from loguru import logger

from config.llm_client import venus_llm, deepseek_llm
from graph.crud import graph_crud


# ─────────────────────────── Cypher 生成 Prompt ───────────────────────────

CYPHER_SYSTEM_PROMPT = """你是一个 Neo4j Cypher 查询专家，专门负责五代十国历史知识图谱的查询。

## 图谱 Schema

### 节点类型
- Person: uid, original_name, aliases(列表), role, loyalty(列表), birth_year, death_year, death_cause, description
- Dynasty: uid, name, founder, capital, start_year, end_year, description
- Event: uid, name, event_type(战争/政变/皇位更替/结盟/背叛事件/其他), year, location, participants(列表), outcome, description
- Place: uid, name, modern_name, description

### 关系类型（动态生成，以下为常见类型）
- FATHER_OF: 父子关系（父→子）
- MOTHER_OF: 母子关系
- ADOPTED_SON: 义子关系（养父→义子）
- SPOUSE: 夫妻
- SIBLING: 兄弟
- BETRAYED: 背叛（背叛者→被背叛者）
- KILLED: 杀害（凶手→被害者）
- REPLACED: 篡位/更替（新君→旧政权）
- SERVED: 效力（臣→主/政权）
- COMMANDED: 统帅
- SUBORDINATE: 下属
- ADVISOR: 谋臣
- ALLIED_WITH: 结盟
- SUCCEEDED: 继位（继任者→前任）
- FOUNDED: 建国（建国者→政权）
- PARTICIPATED_IN: 参与事件（人物→事件）
- OCCURRED_AT: 发生于（事件→地点）
- RIVAL: 对手
- SURRENDERED_TO: 投降
注意：关系类型不限于以上列表，LLM 摄入时可能创建其他自定义关系类型。

## 注意事项
1. 人物搜索时注意别名：WHERE p.original_name = '朱温' OR '朱温' IN p.aliases
2. 返回的 Cypher 必须是可执行的，不要有语法错误
3. 只返回 Cypher 查询语句，不要有任何其他文字或思考过程
4. 确保使用 RETURN 返回有意义的结果
5. 不要使用 CREATE/DELETE/SET/MERGE 等写入操作
"""

CYPHER_USER_TEMPLATE = """请根据以下用户问题，生成一个 Neo4j Cypher 查询来获取答案。

用户问题：{question}

只返回一条 Cypher 查询语句，不要有任何解释或思考过程。"""


# ─────────────────────────── DeepSeek 回答 Prompt ───────────────────────────

ANSWER_SYSTEM_PROMPT = """你是一位精通五代十国（公元907-960年）历史的资深历史学家，深谙这段乱世的政治、军事、人物关系与社会变迁。

你的知识背景：
- 你熟读《旧五代史》《新五代史》《资治通鉴》等核心史料
- 你对五代（后梁、后唐、后晋、后汉、后周）及十国（吴、南唐、吴越、楚、闽、南汉、前蜀、后蜀、荆南、北汉）的历史了如指掌
- 你善于分析人物之间的复杂关系（义父义子、主臣、敌对、联盟等）
- 你能结合历史背景解读事件的因果和深远影响

你现在接入了一个五代十国知识图谱数据库，我会为你提供从知识图谱中检索到的相关数据。

## 回答要求
1. **准确性优先**：核心事实必须以知识图谱提供的数据为依据
2. **深度解读**：在图谱数据基础上，运用你的历史学素养进行分析和背景补充
3. **条理清晰**：涉及多个人物或事件时，按时间顺序或逻辑关系组织回答
4. **客观中立**：如实陈述历史，不偏不倚地评价历史人物
5. **坦诚不足**：如果图谱数据不足以完整回答，请如实说明哪些信息缺失
6. 使用中文回答，语言要流畅自然"""

ANSWER_USER_TEMPLATE = """## 用户问题
{question}

## 知识图谱检索结果
{graph_data}

请基于以上知识图谱数据回答用户的问题。可以适当结合你的历史知识进行分析和补充，但核心事实应以图谱数据为准。"""

FALLBACK_SYSTEM_PROMPT = """你是一位精通五代十国（公元907-960年）历史的资深历史学家。

你熟读《旧五代史》《新五代史》《资治通鉴》等史料，对五代十国的政治格局、军事冲突、人物关系了如指掌。

目前知识图谱中没有找到与该问题直接匹配的数据，请基于你的历史学知识尽力回答。
回答时请明确说明这些信息来自你的知识储备而非知识图谱数据库。
使用中文回答。"""


class GraphRAGEngine:
    """Graph RAG 问答引擎"""

    def answer(self, question: str) -> dict:
        """回答用户问题

        流程:
        1. 先尝试关键词匹配直接查询图谱
        2. 使用维纳斯 LLM 生成 Cypher 查询
        3. 执行查询获取图谱数据
        4. 使用 DeepSeek 基于图谱数据生成回答
        """
        logger.info(f"收到问题: {question}")

        # Step 1: 尝试预定义查询模式
        predefined_result = self._try_predefined_queries(question)

        # Step 2: LLM 生成 Cypher（使用维纳斯）
        cypher = self._generate_cypher(question)
        logger.info(f"生成 Cypher: {cypher}")

        # Step 3: 执行 Cypher 查询
        graph_data = []
        cypher_error = None
        if cypher:
            try:
                graph_data = self._execute_cypher(cypher)
                logger.info(f"Cypher 查询返回 {len(graph_data)} 条结果")
            except Exception as e:
                cypher_error = str(e)
                logger.warning(f"Cypher 执行失败: {e}")

        # 合并预定义查询结果
        if predefined_result:
            graph_data = predefined_result + graph_data

        # Step 4: DeepSeek 生成回答
        if not graph_data:
            answer_text = self._generate_fallback_answer(question)
        else:
            answer_text = self._generate_answer(question, graph_data)

        return {
            "question": question,
            "cypher": cypher,
            "cypher_error": cypher_error,
            "graph_data": graph_data,
            "answer": answer_text,
        }

    def _try_predefined_queries(self, question: str) -> list[dict]:
        """尝试预定义的查询模式"""
        results = []

        # 义子相关查询
        adopted_match = re.search(r'(.{1,5})(的|之)义子|(.{1,5})收了哪些义子', question)
        if adopted_match:
            name = adopted_match.group(1) or adopted_match.group(3)
            persons = graph_crud.get_person_by_name(name)
            if persons:
                uid = persons[0].get("uid") or persons[0].get("p", {}).get("uid")
                if uid:
                    sons = graph_crud.get_adopted_sons(uid)
                    if sons:
                        results.extend(sons)

        # 皇位更替
        if "皇位更替" in question or "更替链" in question or "谁接替" in question or "顺序" in question:
            chain = graph_crud.get_succession_chain()
            if chain:
                results.extend(chain)

        # 家族/家族树
        family_match = re.search(r'(.{1,5})(家族|族谱|亲属)', question)
        if family_match:
            name = family_match.group(1)
            persons = graph_crud.get_person_by_name(name)
            if persons:
                uid = persons[0].get("uid") or persons[0].get("p", {}).get("uid")
                if uid:
                    tree = graph_crud.get_family_tree(uid)
                    if tree:
                        results.extend(tree)

        # 人物关系
        relation_match = re.search(r'(.{1,5})(的关系|有什么关系|相关)', question)
        if relation_match:
            name = relation_match.group(1)
            persons = graph_crud.get_person_by_name(name)
            if persons:
                uid = persons[0].get("uid") or persons[0].get("p", {}).get("uid")
                if uid:
                    rels = graph_crud.get_person_relations(uid)
                    if rels:
                        results.extend(rels)

        return results

    def _generate_cypher(self, question: str) -> str | None:
        """使用维纳斯 LLM 生成 Cypher 查询"""
        try:
            messages = [
                {"role": "system", "content": CYPHER_SYSTEM_PROMPT},
                {"role": "user", "content": CYPHER_USER_TEMPLATE.format(question=question)},
            ]
            raw = venus_llm.chat(messages)
            cypher = self._extract_cypher(raw)
            return cypher
        except Exception as e:
            logger.error(f"Cypher 生成失败: {e}")
            return None

    @staticmethod
    def _extract_cypher(text: str) -> str | None:
        """从 LLM 回复中提取 Cypher 语句"""
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        code_match = re.search(r'```(?:cypher)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        match_pattern = re.search(r'(MATCH\s+.*)', text, re.DOTALL | re.IGNORECASE)
        if match_pattern:
            return match_pattern.group(1).strip()

        call_pattern = re.search(r'(CALL\s+.*)', text, re.DOTALL | re.IGNORECASE)
        if call_pattern:
            return call_pattern.group(1).strip()

        if text.upper().startswith(("MATCH", "CALL", "WITH", "OPTIONAL")):
            return text

        return None

    @staticmethod
    def _execute_cypher(cypher: str) -> list[dict]:
        """执行 Cypher 查询"""
        from graph.connection import neo4j_conn

        if any(
            kw in cypher.upper().split("RETURN")[0] if "RETURN" in cypher.upper() else kw in cypher.upper()
            for kw in ["CREATE ", "DELETE ", "DETACH ", "DROP "]
        ):
            raise ValueError("安全拒绝：查询包含写入操作")

        return neo4j_conn.run_query(cypher)

    def _generate_answer(self, question: str, graph_data: list[dict]) -> str:
        """使用 DeepSeek 基于图谱数据生成回答"""
        graph_text = json.dumps(graph_data, ensure_ascii=False, indent=2, default=str)
        if len(graph_text) > 6000:
            graph_text = graph_text[:6000] + "\n... (数据已截断)"

        try:
            messages = [
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": ANSWER_USER_TEMPLATE.format(
                    question=question, graph_data=graph_text
                )},
            ]
            return deepseek_llm.chat(messages)
        except Exception as e:
            logger.error(f"DeepSeek 回答生成失败: {e}")
            return f"抱歉，回答生成出错：{e}"

    def _generate_fallback_answer(self, question: str) -> str:
        """图谱无结果时的降级回答"""
        try:
            messages = [
                {"role": "system", "content": FALLBACK_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]
            answer = deepseek_llm.chat(messages)
            return "⚠ 注意：以下回答基于通用历史知识，未在知识图谱中找到直接匹配的数据。\n\n" + answer
        except Exception as e:
            return f"抱歉，知识图谱中未找到相关数据，且回答生成出错：{e}"


rag_engine = GraphRAGEngine()

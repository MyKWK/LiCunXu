"""FastAPI Web 服务"""

import json
import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from loguru import logger

from config.settings import settings

app = FastAPI(
    title="五代历史知识图谱与智能问答系统",
    description="Five Dynasties KG-RAG System",
    version="0.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────── 请求/响应模型 ───────────────────


class QuestionRequest(BaseModel):
    question: str


class QuestionResponse(BaseModel):
    question: str
    cypher: str | None = None
    cypher_error: str | None = None
    graph_data: list = []
    answer: str = ""


class PersonSearchRequest(BaseModel):
    name: str


class SeedLoadRequest(BaseModel):
    clear_first: bool = False


# ─────────────────── API 路由 ───────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回可视化前端页面"""
    from api.visualization import get_index_html
    return get_index_html()


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "project": "Five Dynasties KG-RAG"}


@app.post("/api/ask", response_model=QuestionResponse)
async def ask_question(req: QuestionRequest):
    """智能问答接口"""
    from rag.engine import rag_engine
    try:
        result = rag_engine.answer(req.question)
        return QuestionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/stats")
async def graph_stats():
    """图谱统计信息"""
    from graph.crud import graph_crud
    try:
        return graph_crud.get_graph_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/all")
async def graph_all(limit: int = 500):
    """获取全部节点和边（可视化用）"""
    from graph.crud import graph_crud
    try:
        return graph_crud.get_all_nodes_and_edges(limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/graph/search/person")
async def search_person(req: PersonSearchRequest):
    """搜索人物"""
    from graph.crud import graph_crud
    try:
        results = graph_crud.get_person_by_name(req.name)
        if not results:
            results = graph_crud.search_persons_fulltext(req.name)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/person/{person_uid}/relations")
async def person_relations(person_uid: str):
    """获取人物关系"""
    from graph.crud import graph_crud
    try:
        return {"relations": graph_crud.get_person_relations(person_uid)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/person/{person_uid}/events")
async def person_events(person_uid: str):
    """获取人物参与的事件"""
    from graph.crud import graph_crud
    try:
        return {"events": graph_crud.get_person_events(person_uid)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/person/{person_uid}/adopted_sons")
async def person_adopted_sons(person_uid: str):
    """获取义子"""
    from graph.crud import graph_crud
    try:
        return {"adopted_sons": graph_crud.get_adopted_sons(person_uid)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/person/{person_uid}/family")
async def person_family(person_uid: str, depth: int = 3):
    """获取家族树"""
    from graph.crud import graph_crud
    try:
        return {"family": graph_crud.get_family_tree(person_uid, depth)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/succession")
async def succession_chain():
    """五代皇位更替链"""
    from graph.crud import graph_crud
    try:
        return {"chain": graph_crud.get_succession_chain()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/load_seed")
async def load_seed(req: SeedLoadRequest):
    """加载种子数据"""
    from graph.seed_loader import load_seed_data
    try:
        stats = load_seed_data(clear_first=req.clear_first)
        return {"message": "种子数据加载成功", "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/run_ingestion")
async def run_ingestion():
    """运行数据摄入 Pipeline"""
    from ingestion.pipeline import pipeline
    try:
        pipeline.run(clear_db=False)
        from graph.crud import graph_crud
        stats = graph_crud.get_graph_stats()
        return {"message": "摄入完成", "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/event/{event_uid}")
async def event_detail(event_uid: str):
    """获取事件详情 + 书中原文片段"""
    from graph.connection import neo4j_conn
    try:
        # 获取事件基本信息
        results = neo4j_conn.run_query("""
        MATCH (e:Event {uid: $uid})
        RETURN e.uid AS uid, e.name AS name, e.event_type AS event_type,
               e.year AS year, e.location AS location,
               e.participants AS participants, e.outcome AS outcome,
               e.description AS description
        """, {"uid": event_uid})
        if not results:
            raise HTTPException(status_code=404, detail="事件不存在")

        event = results[0]

        # 获取参与的人物
        persons = neo4j_conn.run_query("""
        MATCH (p:Person)-[r:PARTICIPATED_IN]->(e:Event {uid: $uid})
        RETURN p.uid AS uid, p.original_name AS name, p.role AS role,
               r.role AS event_role
        ORDER BY p.original_name
        """, {"uid": event_uid})

        # 搜索书中原文片段
        snippets = _search_book_snippets(event["name"], event.get("description"))

        return {
            "event": event,
            "participants": persons,
            "book_snippets": snippets,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/person/{person_uid}/detail")
async def person_detail(person_uid: str):
    """获取人物完整详情"""
    from graph.connection import neo4j_conn
    try:
        results = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})
        RETURN p.uid AS uid, p.original_name AS name,
               p.aliases AS aliases, p.role AS role,
               p.loyalty AS loyalty, p.description AS description,
               p.birth_year AS birth_year, p.death_year AS death_year,
               p.death_cause AS death_cause
        """, {"uid": person_uid})
        if not results:
            raise HTTPException(status_code=404, detail="人物不存在")

        person = results[0]

        # 获取相关事件（最多50个）
        events = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[:PARTICIPATED_IN]->(e:Event)
        RETURN e.uid AS uid, e.name AS name, e.event_type AS event_type,
               e.year AS year, e.description AS description
        ORDER BY e.year
        LIMIT 50
        """, {"uid": person_uid})

        # 获取关系
        relations = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[r]-(other)
        WHERE NOT type(r) = 'PARTICIPATED_IN'
        RETURN type(r) AS rel_type,
               labels(other) AS other_labels,
               other.uid AS other_uid,
               COALESCE(other.original_name, other.name) AS other_name,
               CASE WHEN startNode(r) = p THEN 'outgoing' ELSE 'incoming' END AS direction
        """, {"uid": person_uid})

        return {
            "person": person,
            "events": events,
            "relations": relations,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _search_book_snippets(event_name: str, description: str | None = None,
                          max_snippets: int = 3, context_chars: int = 200) -> list[dict]:
    """在书中搜索与事件相关的原文片段"""
    import os

    books_dir = settings.PROJECT_ROOT / settings.BOOKS_DIR
    if not books_dir.exists():
        return []

    # 构建搜索关键词
    keywords = []
    if event_name:
        # 从事件名提取关键词（去掉通用词）
        clean_name = event_name.replace("之战", "").replace("之变", "").replace("事件", "")
        if len(clean_name) >= 2:
            keywords.append(clean_name)
        keywords.append(event_name)

    # 从 description 中提取人名（2-4字）
    if description:
        import re
        # 提取前几个关键词
        words = re.findall(r'[\u4e00-\u9fff]{2,4}', description[:100])
        keywords.extend(words[:3])

    if not keywords:
        return []

    snippets = []
    seen_texts = set()

    for book_file in sorted(books_dir.glob("*.txt")):
        try:
            with open(book_file, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue

        book_name = book_file.stem

        for kw in keywords:
            if len(snippets) >= max_snippets:
                break

            pos = 0
            while pos < len(text) and len(snippets) < max_snippets:
                idx = text.find(kw, pos)
                if idx == -1:
                    break

                start = max(0, idx - context_chars)
                end = min(len(text), idx + len(kw) + context_chars)
                snippet_text = text[start:end].strip()

                # 去重
                sig = snippet_text[:80]
                if sig not in seen_texts:
                    seen_texts.add(sig)
                    # 标记关键词
                    snippets.append({
                        "book": book_name,
                        "keyword": kw,
                        "text": snippet_text,
                    })

                pos = idx + len(kw) + context_chars

    return snippets[:max_snippets]


# ─────────────────── 关系类型英→中映射 ───────────────────

RELATION_TYPE_CN = {
    "FATHER_OF": "父→子",
    "MOTHER_OF": "母→子",
    "ADOPTED_SON": "义父→义子",
    "SPOUSE": "夫妻",
    "SIBLING": "兄弟",
    "BETRAYED": "背叛",
    "KILLED": "杀害",
    "REPLACED": "篡位",
    "SERVED": "效力",
    "COMMANDED": "统帅",
    "SUBORDINATE": "臣属",
    "ADVISOR": "谋士",
    "ALLIED_WITH": "结盟",
    "RIVAL": "敌人",
    "SURRENDERED_TO": "投降",
    "SUCCEEDED": "继位",
    "FOUNDED": "创建",
    "PARTICIPATED_IN": "参与",
    "OCCURRED_AT": "发生于",
    "FRIEND": "朋友",
    "GRANDFATHER_OF": "祖→孙",
    "GRANDMOTHER_OF": "祖母→孙",
    "UNCLE_OF": "叔→侄",
    "NEPHEW_OF": "侄→叔",
    "CAPTURED": "俘获",
    "CAPTURED_BY": "被俘",
    "SUPPORTED": "支持",
    "DEFEATED": "击败",
    "DEFEATED_BY": "被击败",
    "APPOINTED": "任命",
    "SAVED": "救助",
    "LOYAL_TO": "忠于",
    "RULER": "统治者",
    "RULER_OF": "统治",
    "ATTACKED": "进攻",
    "PROTECTED": "庇护",
    "COLLEAGUE": "同僚",
    "LOVER": "情人",
    "REBELLED_AGAINST": "叛变",
    "SON_OF": "子→父",
    "SON": "子",
    "DESCENDANT": "后裔",
    "BROTHER": "兄弟",
    "RELATIVE": "亲属",
    "FOUNDER_OF": "创立",
    "TRUSTED": "信任",
    "INFLUENCED": "影响",
    "IMPRISONED": "囚禁",
    "ARRESTED": "逮捕",
    "PUNISHED": "惩罚",
    "REWARDED": "赏赐",
    "RECOMMENDED": "举荐",
    "REJECTED": "拒绝",
    "SUSPECTED": "猜忌",
    "THREATENED": "威胁",
    "FEARED": "畏惧",
    "RESPECTED": "敬重",
    "INSULTED": "侮辱",
    "GOVERNED": "治理",
    "GOVERNOR": "治理",
    "GOVERNOR_OF": "治理",
    "BUILT": "建造",
    "CONQUERED": "征服",
    "DEFENDED": "防御",
    "SURRENDERED": "投降",
    "GRANTED_TITLE": "授爵",
    "RENAMED": "赐名",
    "SENT": "派遣",
    "HOLDING_HOSTAGE": "扣押人质",
    "HOSTAGE": "人质",
    "IDOL": "仰慕",
    "ADMIRER": "仰慕者",
    "ADMIRATION": "仰慕",
    "CLASSMATE": "同窗",
    "DIPLOMAT": "外交",
    "DESECRATED": "亵渎",
    "MOURNED": "哀悼",
    "PRAISED": "赞赏",
    "CRITICIZED": "批评",
    "WARNED": "警告",
    "SHELTERED": "庇护",
    "SHELTERED_BY": "被庇护",
    "OPPOSED": "反对",
    "SUPPORTED_BY": "被支持",
    "DAUGHTER_OF": "女→父",
    "PARENT_OF": "亲→子",
    "GRANDSON": "孙",
    "GRANDSON_OF": "孙→祖",
    "ADOPTED": "收养",
    "ADOPTED_GRANDSON": "养孙",
    "ANCESTOR_OF": "先祖→后裔",
    "ANCESTOR": "先祖",
    "DESCENDED_FROM": "后裔→先祖",
    "LEADER_OF": "首领",
    "TEACHER": "师",
    "STUDENT": "徒",
    "DISCIPLE": "弟子",
    "MENTOR": "导师",
    "ENEMY_OF": "敌对",
    "PROPOSED_TO": "求婚",
    "KILLED_BY": "被杀",
    "DIED_IN": "死于",
    "FLED_TO": "逃往",
    "PRECEDED": "前任",
    "PRECEDED_BY": "前任",
    "PREDECESSOR": "前任",
    "SUCCESSOR": "继任",
}


@app.get("/api/relation_types_cn")
async def get_relation_types_cn():
    """获取关系类型中文映射"""
    return RELATION_TYPE_CN


# ─────────────────── LLM 总结 + 缓存 ───────────────────

SUMMARIES_DIR = settings.PROJECT_ROOT / "data" / "summaries"
SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)


def _get_summary_cache_path(entity_type: str, uid: str) -> Path:
    """获取总结缓存文件路径"""
    safe_uid = uid.replace("/", "_")
    return SUMMARIES_DIR / f"{entity_type}_{safe_uid}.json"


def _load_summary_cache(entity_type: str, uid: str) -> str | None:
    """从缓存加载总结"""
    path = _get_summary_cache_path(entity_type, uid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("summary")
        except Exception:
            return None
    return None


def _save_summary_cache(entity_type: str, uid: str, summary: str):
    """保存总结到缓存"""
    path = _get_summary_cache_path(entity_type, uid)
    data = {"uid": uid, "type": entity_type, "summary": summary}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


PERSON_SUMMARY_PROMPT = """你是一位精通五代十国（公元907-960年）历史的资深历史学家。

请根据以下从知识图谱中检索到的人物信息，撰写一段该人物的**生平总结**。

## 输出要求
1. **使用 Markdown 格式**输出
2. 按时间线组织叙述，清晰展现人物生平发展脉络
3. **重要人物名字加粗**（如 **李存勖**、**朱温** 等）
4. 涉及重大事件时简要说明因果关系
5. 篇幅控制在 200-400 字之间，言简意赅但不遗漏关键信息
6. 如果信息有限，据实说明，不要编造
7. 使用中文"""

PERSON_SUMMARY_USER_TEMPLATE = """## 人物基本信息
- 姓名：{name}
- 别名：{aliases}
- 角色：{role}
- 效力势力：{loyalty}
- 生卒：{birth} — {death}
- 死因：{death_cause}
- 简述：{description}

## 人物关系（共{rel_count}条）
{relations_text}

## 参与事件（共{event_count}件，按时间排序）
{events_text}

请根据以上信息，撰写该人物的生平总结。"""

EVENT_SUMMARY_PROMPT = """你是一位精通五代十国（公元907-960年）历史的资深历史学家。

请根据以下从知识图谱中检索到的事件信息，撰写一段该事件的**详细总结**。

## 输出要求
1. **使用 Markdown 格式**输出
2. 重点阐述事件的**起因、经过、结果**，以及对后续局势的影响
3. **重要人物名字加粗**（如 **李存勖**、**朱温** 等）
4. 如涉及多个阶段，按时间顺序叙述
5. 篇幅控制在 150-300 字之间
6. 如果信息有限，据实说明，不要编造
7. 使用中文"""

EVENT_SUMMARY_USER_TEMPLATE = """## 事件基本信息
- 事件名称：{name}
- 事件类型：{event_type}
- 年份：{year}
- 地点：{location}
- 结果：{outcome}
- 描述：{description}

## 参与人物（共{participant_count}人）
{participants_text}

## 相关书中原文片段
{snippets_text}

请根据以上信息，撰写该事件的详细总结。"""


def _build_relation_cn(rel: dict, person_name: str) -> str:
    """构建单条关系的中文描述"""
    rt = rel.get("rel_type", "")
    cn = RELATION_TYPE_CN.get(rt, rt)
    other = rel.get("other_name", "?")
    direction = rel.get("direction", "outgoing")
    if direction == "outgoing":
        return f"{person_name} —[{cn}]→ {other}"
    else:
        return f"{other} —[{cn}]→ {person_name}"


@app.get("/api/graph/person/{person_uid}/summary")
async def person_summary(person_uid: str):
    """获取人物生平总结（带缓存）"""
    # 先查缓存
    cached = _load_summary_cache("person", person_uid)
    if cached:
        return {"uid": person_uid, "summary": cached, "from_cache": True}

    from graph.connection import neo4j_conn
    from config.llm_client import deepseek_llm

    try:
        # 获取人物信息
        results = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})
        RETURN p.uid AS uid, p.original_name AS name,
               p.aliases AS aliases, p.role AS role,
               p.loyalty AS loyalty, p.description AS description,
               p.birth_year AS birth_year, p.death_year AS death_year,
               p.death_cause AS death_cause
        """, {"uid": person_uid})
        if not results:
            raise HTTPException(status_code=404, detail="人物不存在")

        p = results[0]

        # 获取关系
        relations = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[r]-(other)
        WHERE NOT type(r) = 'PARTICIPATED_IN'
        RETURN type(r) AS rel_type,
               COALESCE(other.original_name, other.name) AS other_name,
               CASE WHEN startNode(r) = p THEN 'outgoing' ELSE 'incoming' END AS direction
        LIMIT 50
        """, {"uid": person_uid})

        # 获取事件
        events = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[:PARTICIPATED_IN]->(e:Event)
        RETURN e.name AS name, e.year AS year, e.event_type AS event_type,
               e.description AS description
        ORDER BY e.year
        LIMIT 30
        """, {"uid": person_uid})

        # 构建 LLM 输入
        relations_text = "\n".join(
            f"- {_build_relation_cn(r, p['name'])}" for r in relations
        ) if relations else "（暂无关系数据）"

        events_text = "\n".join(
            f"- {'[' + str(e['year']) + '] ' if e.get('year') else ''}"
            f"{e['name']}"
            f"{'（' + e['event_type'] + '）' if e.get('event_type') else ''}"
            f"{'：' + e['description'][:60] if e.get('description') else ''}"
            for e in events
        ) if events else "（暂无事件数据）"

        user_msg = PERSON_SUMMARY_USER_TEMPLATE.format(
            name=p["name"],
            aliases="、".join(p.get("aliases") or []) or "无",
            role=p.get("role") or "未知",
            loyalty=" → ".join(p.get("loyalty") or []) or "未知",
            birth=p.get("birth_year") or "?",
            death=p.get("death_year") or "?",
            death_cause=p.get("death_cause") or "未知",
            description=p.get("description") or "无",
            rel_count=len(relations),
            relations_text=relations_text,
            event_count=len(events),
            events_text=events_text,
        )

        messages = [
            {"role": "system", "content": PERSON_SUMMARY_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        summary = deepseek_llm.chat(messages, temperature=0.3, max_tokens=2048)

        # 保存缓存
        _save_summary_cache("person", person_uid, summary)
        logger.info(f"生成人物总结: {p['name']} ({person_uid})")

        return {"uid": person_uid, "summary": summary, "from_cache": False}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"人物总结生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/event/{event_uid}/summary")
async def event_summary(event_uid: str):
    """获取事件总结（带缓存）"""
    # 先查缓存
    cached = _load_summary_cache("event", event_uid)
    if cached:
        return {"uid": event_uid, "summary": cached, "from_cache": True}

    from graph.connection import neo4j_conn
    from config.llm_client import deepseek_llm

    try:
        # 获取事件信息
        results = neo4j_conn.run_query("""
        MATCH (e:Event {uid: $uid})
        RETURN e.uid AS uid, e.name AS name, e.event_type AS event_type,
               e.year AS year, e.location AS location,
               e.participants AS participants, e.outcome AS outcome,
               e.description AS description
        """, {"uid": event_uid})
        if not results:
            raise HTTPException(status_code=404, detail="事件不存在")

        ev = results[0]

        # 获取参与人物
        persons = neo4j_conn.run_query("""
        MATCH (p:Person)-[r:PARTICIPATED_IN]->(e:Event {uid: $uid})
        RETURN p.original_name AS name, p.role AS role, r.role AS event_role
        ORDER BY p.original_name
        """, {"uid": event_uid})

        # 搜索原文片段
        snippets = _search_book_snippets(ev["name"], ev.get("description"), max_snippets=2)

        # 构建 LLM 输入
        participants_text = "\n".join(
            f"- {p['name']}{'（' + p['role'] + '）' if p.get('role') else ''}"
            f"{'，事件角色：' + p['event_role'] if p.get('event_role') else ''}"
            for p in persons
        ) if persons else "（暂无参与人物数据）"

        snippets_text = "\n".join(
            f"——《{s['book']}》：「{s['text'][:200]}」" for s in snippets
        ) if snippets else "（暂无原文片段）"

        user_msg = EVENT_SUMMARY_USER_TEMPLATE.format(
            name=ev["name"],
            event_type=ev.get("event_type") or "未知",
            year=ev.get("year") or "未知",
            location=ev.get("location") or "未知",
            outcome=ev.get("outcome") or "未知",
            description=ev.get("description") or "无",
            participant_count=len(persons),
            participants_text=participants_text,
            snippets_text=snippets_text,
        )

        messages = [
            {"role": "system", "content": EVENT_SUMMARY_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        summary = deepseek_llm.chat(messages, temperature=0.3, max_tokens=1500)

        # 保存缓存
        _save_summary_cache("event", event_uid, summary)
        logger.info(f"生成事件总结: {ev['name']} ({event_uid})")

        return {"uid": event_uid, "summary": summary, "from_cache": False}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"事件总结生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────── 静态文件（放在最后，避免拦截 API 路由） ───────────────────

static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

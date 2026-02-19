"""图谱 CRUD 操作 - 支持基于名字/别名的智能合并

核心设计：
- 人物插入前，先用所有名字（original_name + aliases）去 Neo4j 查询
- 如果找到匹配（对方的 name 或 aliases 包含任一名字），就合并到已有节点
- 如果找不到，才创建新节点
- 关系创建时，也通过名字查找人物 uid，而非依赖 LLM 给出的 uid

防污染机制：
- 通用称号黑名单（"太宗"、"晋王"、"太子"等）不参与合并匹配
- 匹配时优先用 original_name 精确匹配
- 只有当 original_name 匹配或具体别名匹配时才合并
"""

from loguru import logger

from models.entities import Dynasty, Event, Person, Place, Relation
from graph.connection import neo4j_conn


# ━━━━━━━━ 通用称号黑名单：这些名字不应用于跨人物的合并匹配 ━━━━━━━━
# 这些称号在不同时期可能指向不同的人物，仅凭这些词不应触发合并
AMBIGUOUS_TITLES = {
    # 通用尊号/庙号
    "太宗", "太祖", "世宗", "高祖", "中宗", "明宗", "穆宗", "庄宗",
    "文宗", "武宗", "宣宗", "哀帝", "废帝", "少帝", "末帝",
    # 通用称号
    "皇帝", "天子", "新天子", "大宋天子", "大周皇帝", "皇太子", "太子",
    "皇太弟", "晋王", "秦王", "齐王", "赵王", "魏王", "楚王", "吴王",
    "蜀王", "梁王", "燕王", "汉王", "周王", "宋王", "鲁王",
    "皇后", "太后", "太妃", "贵妃", "淑妃", "贤妃",
    # 通用亲属称谓
    "太子", "世子", "长子", "次子", "三子", "老大", "老二", "老三", "老四",
    "先王", "先帝",
    # 通用官职
    "宰相", "枢密使", "节度使", "刺史", "侍中",
    # 模糊指代
    "唐主", "梁主", "晋主", "汉主", "周主", "蜀主", "契丹主",
    "辽主", "吴主",
    "伶人", "优伶", "伶伦", "住持和尚", "法师",
}


class GraphCRUD:
    """知识图谱增删改查操作 - 支持智能人物合并"""

    # ━━━━━━━━━━━━━━━ 人物名字匹配与合并 ━━━━━━━━━━━━━━━

    @staticmethod
    def _filter_ambiguous_names(names: set[str]) -> set[str]:
        """过滤掉通用称号，只保留具体的人名用于合并匹配"""
        specific = set()
        for n in names:
            if not n:
                continue
            # 跳过通用称号
            if n in AMBIGUOUS_TITLES:
                continue
            # 跳过单字名（如"倍"、"衍"等，太容易混淆）
            if len(n) == 1:
                continue
            specific.add(n)
        return specific

    @staticmethod
    def find_person_by_any_name(names: set[str], use_filter: bool = True) -> dict | None:
        """通过任意名字（name 或 aliases）查找已有人物节点

        改进：先用 original_name 精确匹配，再用过滤后的具体别名匹配。
        通用称号（太宗、晋王等）不参与合并匹配。

        Args:
            names: 要查找的名字集合
            use_filter: 是否过滤通用称号（默认 True）

        Returns:
            匹配到的人物记录 dict，或 None
        """
        if not names:
            return None

        # 过滤通用称号
        if use_filter:
            specific_names = GraphCRUD._filter_ambiguous_names(names)
        else:
            specific_names = {n for n in names if n}

        if not specific_names:
            return None

        names_list = sorted(specific_names)

        # 优先用 original_name 精确匹配（最可靠）
        cypher_exact = """
        MATCH (p:Person)
        WHERE p.original_name IN $names
        RETURN p.uid AS uid, p.original_name AS original_name,
               p.aliases AS aliases, p.role AS role,
               p.loyalty AS loyalty, p.description AS description,
               p.birth_year AS birth_year, p.death_year AS death_year,
               p.death_cause AS death_cause
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_exact, {"names": names_list})
        if results:
            return results[0]

        # 再用具体别名匹配（排除通用称号后的）
        cypher_alias = """
        MATCH (p:Person)
        WHERE any(alias IN p.aliases WHERE alias IN $names)
        RETURN p.uid AS uid, p.original_name AS original_name,
               p.aliases AS aliases, p.role AS role,
               p.loyalty AS loyalty, p.description AS description,
               p.birth_year AS birth_year, p.death_year AS death_year,
               p.death_cause AS death_cause
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_alias, {"names": names_list})
        return results[0] if results else None

    @staticmethod
    def merge_person(person: Person) -> str:
        """智能合并人物节点（含防污染机制）

        流程：
        1. 收集新人物的所有名字（original_name + aliases）
        2. 过滤掉通用称号（太宗、晋王等），仅用具体名字做匹配
        3. 在 Neo4j 中查找是否有匹配
        4. 如果找到且匹配可信 → 合并别名、更新信息
        5. 如果没找到 → 创建新节点

        Returns:
            最终使用的 uid
        """
        all_names = person.all_names()
        existing = GraphCRUD.find_person_by_any_name(all_names)

        if existing:
            # ────── 合并到已有节点 ──────
            existing_uid = existing["uid"]
            existing_aliases = set(existing.get("aliases") or [])
            existing_aliases.add(existing.get("original_name", ""))

            # 合并所有名字到 aliases（此处不过滤通用称号，它们仍然作为别名保留）
            new_aliases = existing_aliases | all_names
            # 主名不需要出现在 aliases 中
            final_original = existing.get("original_name")
            new_aliases.discard(final_original)
            new_aliases.discard("")

            # 防污染：限制别名总数不超过 25 个
            if len(new_aliases) > 25:
                # 优先保留已有别名
                added = all_names - existing_aliases - {final_original}
                logger.warning(
                    f"⚠️ 合并后别名数 ({len(new_aliases)}) 超过上限，"
                    f"跳过合并 [{person.original_name}] → [{final_original}]，"
                    f"改为创建新节点"
                )
                # 别名过多说明可能是误合并，创建新节点
                cypher = """
                MERGE (p:Person {uid: $uid})
                SET p += $props
                """
                props = person.neo4j_properties()
                uid = props.pop("uid")
                neo4j_conn.run_write(cypher, {"uid": uid, "props": props})
                logger.debug(f"新建人物 [{person.original_name}] (uid={uid})")
                return uid

            # 合并 loyalty
            existing_loyalty = existing.get("loyalty") or []
            merged_loyalty = list(dict.fromkeys(existing_loyalty + person.loyalty))

            # 合并 description（取更长的）
            existing_desc = existing.get("description") or ""
            new_desc = person.description or ""
            final_desc = new_desc if len(new_desc) > len(existing_desc) else existing_desc

            # 更新节点
            update_cypher = """
            MATCH (p:Person {uid: $uid})
            SET p.aliases = $aliases,
                p.loyalty = $loyalty,
                p.description = $desc
            """
            params = {
                "uid": existing_uid,
                "aliases": sorted(new_aliases),
                "loyalty": merged_loyalty,
                "desc": final_desc,
            }

            # 补充缺失的年份信息
            if person.birth_year and not existing.get("birth_year"):
                update_cypher += ", p.birth_year = $birth_year"
                params["birth_year"] = person.birth_year
            if person.death_year and not existing.get("death_year"):
                update_cypher += ", p.death_year = $death_year"
                params["death_year"] = person.death_year
            if person.death_cause and not existing.get("death_cause"):
                update_cypher += ", p.death_cause = $death_cause"
                params["death_cause"] = person.death_cause
            if person.role and person.role != "其他" and existing.get("role") in (None, "其他", ""):
                update_cypher += ", p.role = $role"
                params["role"] = person.role

            neo4j_conn.run_write(update_cypher, params)

            logger.debug(
                f"合并人物 [{person.original_name}] → 已有节点 [{final_original}] "
                f"(uid={existing_uid}), 新别名: {new_aliases - existing_aliases}"
            )
            return existing_uid
        else:
            # ────── 创建新节点 ──────
            cypher = """
            MERGE (p:Person {uid: $uid})
            SET p += $props
            """
            props = person.neo4j_properties()
            uid = props.pop("uid")
            neo4j_conn.run_write(cypher, {"uid": uid, "props": props})
            logger.debug(f"新建人物 [{person.original_name}] (uid={uid}), 别名: {person.aliases}")
            return uid

    @staticmethod
    def resolve_node_uid(name: str) -> str | None:
        """通过名字解析出任意节点的 uid

        先查 Person（original_name 和 aliases），
        再查 Dynasty（name），再查 Event（name），再查 Place（name）。
        """
        if not name:
            return None

        # 1. 查 Person
        cypher = """
        MATCH (p:Person)
        WHERE p.original_name = $name OR $name IN p.aliases
        RETURN p.uid AS uid
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher, {"name": name})
        if results:
            return results[0]["uid"]

        # 2. 查 Dynasty
        cypher = "MATCH (d:Dynasty) WHERE d.name = $name RETURN d.uid AS uid LIMIT 1"
        results = neo4j_conn.run_query(cypher, {"name": name})
        if results:
            return results[0]["uid"]

        # 3. 查 Event
        cypher = "MATCH (e:Event) WHERE e.name = $name RETURN e.uid AS uid LIMIT 1"
        results = neo4j_conn.run_query(cypher, {"name": name})
        if results:
            return results[0]["uid"]

        # 4. 查 Place
        cypher = "MATCH (pl:Place) WHERE pl.name = $name RETURN pl.uid AS uid LIMIT 1"
        results = neo4j_conn.run_query(cypher, {"name": name})
        if results:
            return results[0]["uid"]

        return None

    @staticmethod
    def resolve_person_uid(name: str) -> str | None:
        """通过名字解析出人物的 uid（仅查 Person）"""
        if not name:
            return None
        cypher = """
        MATCH (p:Person)
        WHERE p.original_name = $name OR $name IN p.aliases
        RETURN p.uid AS uid
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher, {"name": name})
        return results[0]["uid"] if results else None

    # ━━━━━━━━━━━━━━━ 标准 UPSERT ━━━━━━━━━━━━━━━

    @staticmethod
    def upsert_person(person: Person):
        """创建或更新人物节点（简单模式，仅按 uid 合并）"""
        cypher = """
        MERGE (p:Person {uid: $uid})
        SET p += $props
        """
        props = person.neo4j_properties()
        uid = props.pop("uid")
        neo4j_conn.run_write(cypher, {"uid": uid, "props": props})

    @staticmethod
    def upsert_dynasty(dynasty: Dynasty):
        """创建或更新政权节点"""
        cypher = """
        MERGE (d:Dynasty {uid: $uid})
        SET d += $props
        """
        props = dynasty.neo4j_properties()
        uid = props.pop("uid")
        neo4j_conn.run_write(cypher, {"uid": uid, "props": props})

    @staticmethod
    def upsert_event(event: Event):
        """创建或更新事件节点"""
        cypher = """
        MERGE (e:Event {uid: $uid})
        SET e += $props
        """
        props = event.neo4j_properties()
        uid = props.pop("uid")
        neo4j_conn.run_write(cypher, {"uid": uid, "props": props})

    @staticmethod
    def upsert_place(place: Place):
        """创建或更新地点节点"""
        cypher = """
        MERGE (pl:Place {uid: $uid})
        SET pl += $props
        """
        props = place.neo4j_properties()
        uid = props.pop("uid")
        neo4j_conn.run_write(cypher, {"uid": uid, "props": props})

    # ━━━━━━━━━━━━━━━ 智能关系创建 ━━━━━━━━━━━━━━━

    @staticmethod
    def create_relation_by_name(relation: Relation):
        """通过人名创建关系（自动解析 uid）

        与旧版 create_relation 不同：
        - 旧版需要 source_uid 和 target_uid（依赖 LLM 给出一致的 uid）
        - 新版使用 source（名字）和 target（名字），自动去 Neo4j 查 uid
        """
        source_uid = GraphCRUD.resolve_node_uid(relation.source)
        target_uid = GraphCRUD.resolve_node_uid(relation.target)

        if not source_uid:
            logger.warning(f"关系创建失败：找不到源人物 [{relation.source}]")
            return False
        if not target_uid:
            logger.warning(f"关系创建失败：找不到目标人物 [{relation.target}]")
            return False

        # 清洗关系类型（统一大写，去除空格）
        rel_type = relation.relation_type.upper().replace(" ", "_").replace("-", "_")
        # 确保是合法的 Neo4j 关系类型名称
        rel_type = "".join(c if c.isalnum() or c == "_" else "_" for c in rel_type)
        if not rel_type:
            rel_type = "RELATED_TO"

        cypher = f"""
        MATCH (a {{uid: $source_uid}})
        MATCH (b {{uid: $target_uid}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $props
        """
        try:
            neo4j_conn.run_write(cypher, {
                "source_uid": source_uid,
                "target_uid": target_uid,
                "props": relation.neo4j_properties(),
            })
            return True
        except Exception as e:
            logger.warning(f"关系写入失败 [{relation.source}]-[{rel_type}]->[{relation.target}]: {e}")
            return False

    @staticmethod
    def link_event_participant(event_uid: str, person_name: str, role: str = "参与者"):
        """将人物与事件关联（通过人名查找）"""
        person_uid = GraphCRUD.resolve_person_uid(person_name)
        if not person_uid:
            # 尝试直接用 uid 格式
            cypher_check = "MATCH (p:Person {uid: $uid}) RETURN p.uid AS uid"
            check = neo4j_conn.run_query(cypher_check, {"uid": person_name})
            if check:
                person_uid = person_name
            else:
                return

        cypher = """
        MATCH (e:Event {uid: $event_uid})
        MATCH (p:Person {uid: $person_uid})
        MERGE (p)-[r:PARTICIPATED_IN]->(e)
        SET r.role = $role
        """
        neo4j_conn.run_write(cypher, {
            "event_uid": event_uid,
            "person_uid": person_uid,
            "role": role,
        })

    @staticmethod
    def link_event_place(event_uid: str, place_uid: str):
        """将事件与地点关联"""
        cypher = """
        MATCH (e:Event {uid: $event_uid})
        MATCH (pl:Place {uid: $place_uid})
        MERGE (e)-[:OCCURRED_AT]->(pl)
        """
        neo4j_conn.run_write(cypher, {
            "event_uid": event_uid,
            "place_uid": place_uid,
        })

    @staticmethod
    def link_dynasty_founder(dynasty_uid: str, person_name: str):
        """将政权与创建者关联（通过人名查找）"""
        person_uid = GraphCRUD.resolve_person_uid(person_name)
        if not person_uid:
            return
        cypher = """
        MATCH (d:Dynasty {uid: $dynasty_uid})
        MATCH (p:Person {uid: $person_uid})
        MERGE (p)-[:FOUNDED]->(d)
        """
        neo4j_conn.run_write(cypher, {
            "dynasty_uid": dynasty_uid,
            "person_uid": person_uid,
        })

    # ━━━━━━━━━━━━━━━ 查询 ━━━━━━━━━━━━━━━

    @staticmethod
    def get_person_by_name(name: str) -> list[dict]:
        """通过名字或别名查找人物"""
        cypher = """
        MATCH (p:Person)
        WHERE p.original_name = $name OR $name IN p.aliases
        RETURN p.uid AS uid, p.original_name AS name,
               p.aliases AS aliases, p.role AS role,
               p.loyalty AS loyalty, p.description AS description,
               p.birth_year AS birth_year, p.death_year AS death_year,
               p.death_cause AS death_cause
        """
        return neo4j_conn.run_query(cypher, {"name": name})

    @staticmethod
    def get_person_relations(person_uid: str) -> list[dict]:
        """获取人物的所有关系"""
        cypher = """
        MATCH (p:Person {uid: $uid})-[r]-(other)
        RETURN type(r) AS rel_type, r AS rel_props,
               labels(other) AS other_labels,
               other.uid AS other_uid,
               COALESCE(other.original_name, other.name) AS other_name,
               CASE WHEN startNode(r) = p THEN 'outgoing' ELSE 'incoming' END AS direction
        """
        return neo4j_conn.run_query(cypher, {"uid": person_uid})

    @staticmethod
    def get_adopted_sons(person_uid: str) -> list[dict]:
        """获取某人的义子"""
        cypher = """
        MATCH (p:Person {uid: $uid})-[:ADOPTED_SON]->(son:Person)
        RETURN son.uid AS uid, son.original_name AS name,
               son.aliases AS aliases, son.death_cause AS death_cause,
               son.description AS description
        """
        return neo4j_conn.run_query(cypher, {"uid": person_uid})

    @staticmethod
    def get_succession_chain() -> list[dict]:
        """获取五代皇位更替链"""
        cypher = """
        MATCH (p:Person)-[r:REPLACED|SUCCEEDED]->(target)
        RETURN p.original_name AS person_name, p.uid AS person_uid,
               COALESCE(target.name, target.original_name) AS target_name,
               target.uid AS target_uid,
               type(r) AS rel_type,
               r.year AS year, r.description AS description
        ORDER BY r.year
        """
        return neo4j_conn.run_query(cypher)

    @staticmethod
    def get_family_tree(person_uid: str, depth: int = 3) -> list[dict]:
        """获取家族关系树"""
        cypher = f"""
        MATCH path = (p:Person {{uid: $uid}})-[:FATHER_OF|ADOPTED_SON|SPOUSE|SIBLING*1..{depth}]-(related:Person)
        UNWIND relationships(path) AS r
        WITH startNode(r) AS from_node, endNode(r) AS to_node, type(r) AS rel_type
        RETURN DISTINCT from_node.uid AS from_uid, from_node.original_name AS from_name,
               to_node.uid AS to_uid, to_node.original_name AS to_name,
               rel_type
        """
        return neo4j_conn.run_query(cypher, {"uid": person_uid})

    @staticmethod
    def search_persons_fulltext(query: str, limit: int = 10) -> list[dict]:
        """全文搜索人物"""
        cypher = """
        CALL db.index.fulltext.queryNodes("person_fulltext_index", $query)
        YIELD node, score
        RETURN node.uid AS uid, node.original_name AS name,
               node.aliases AS aliases, node.description AS description,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        return neo4j_conn.run_query(cypher, {"query": query, "limit": limit})

    @staticmethod
    def get_graph_stats() -> dict:
        """获取图谱统计信息"""
        stats = {}
        for label in ["Person", "Dynasty", "Event", "Place"]:
            result = neo4j_conn.run_query(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            stats[label] = result[0]["cnt"] if result else 0
        rel_result = neo4j_conn.run_query("MATCH ()-[r]->() RETURN count(r) AS cnt")
        stats["Relation"] = rel_result[0]["cnt"] if rel_result else 0
        return stats

    @staticmethod
    def get_all_nodes_and_edges(limit: int = 500) -> dict:
        """获取所有节点和边（用于可视化）"""
        nodes_cypher = """
        MATCH (n)
        RETURN n.uid AS uid,
               COALESCE(n.original_name, n.name) AS name,
               labels(n) AS labels,
               properties(n) AS props
        LIMIT $limit
        """
        edges_cypher = """
        MATCH (a)-[r]->(b)
        RETURN a.uid AS source, b.uid AS target,
               type(r) AS rel_type, properties(r) AS props
        LIMIT $limit
        """
        nodes = neo4j_conn.run_query(nodes_cypher, {"limit": limit})
        edges = neo4j_conn.run_query(edges_cypher, {"limit": limit})
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def get_person_count() -> int:
        """获取当前人物节点总数"""
        result = neo4j_conn.run_query("MATCH (n:Person) RETURN count(n) AS cnt")
        return result[0]["cnt"] if result else 0

    @staticmethod
    def get_all_person_names() -> list[dict]:
        """获取所有人物的名字和别名（用于向 LLM 提供上下文）"""
        cypher = """
        MATCH (p:Person)
        RETURN p.original_name AS name, p.aliases AS aliases
        ORDER BY p.original_name
        """
        return neo4j_conn.run_query(cypher)

    @staticmethod
    def get_person_events(person_uid: str) -> list[dict]:
        """获取人物参与的所有事件"""
        cypher = """
        MATCH (p:Person {uid: $uid})-[:PARTICIPATED_IN]->(e:Event)
        RETURN e.uid AS uid, e.name AS name, e.event_type AS event_type,
               e.year AS year, e.location AS location,
               e.outcome AS outcome, e.description AS description
        ORDER BY e.year
        """
        return neo4j_conn.run_query(cypher, {"uid": person_uid})


graph_crud = GraphCRUD()

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

from models.entities import Dynasty, Event, OfficialTitle, Person, Place, Relation
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

        # 2. 查 Dynasty（name 或 aliases）
        cypher = """
        MATCH (d:Dynasty)
        WHERE d.name = $name OR $name IN d.aliases
        RETURN d.uid AS uid LIMIT 1
        """
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

        # 5. 查 OfficialTitle（name 或 aliases）
        cypher = """
        MATCH (t:OfficialTitle)
        WHERE t.name = $name OR $name IN t.aliases
        RETURN t.uid AS uid LIMIT 1
        """
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
    def find_dynasty_by_any_name(names: set[str]) -> dict | None:
        """通过任意名字（name 或 aliases）查找已有政权/势力节点

        Args:
            names: 要查找的名字集合

        Returns:
            匹配到的政权记录 dict，或 None
        """
        if not names:
            return None
        names_list = sorted(n for n in names if n)
        if not names_list:
            return None

        # 优先用 name 精确匹配
        cypher_exact = """
        MATCH (d:Dynasty)
        WHERE d.name IN $names
        RETURN d.uid AS uid, d.name AS name, d.aliases AS aliases,
               d.faction_type AS faction_type, d.founder AS founder,
               d.capital AS capital, d.start_year AS start_year,
               d.end_year AS end_year, d.predecessor AS predecessor,
               d.description AS description
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_exact, {"names": names_list})
        if results:
            return results[0]

        # 再用 aliases 匹配
        cypher_alias = """
        MATCH (d:Dynasty)
        WHERE any(alias IN d.aliases WHERE alias IN $names)
        RETURN d.uid AS uid, d.name AS name, d.aliases AS aliases,
               d.faction_type AS faction_type, d.founder AS founder,
               d.capital AS capital, d.start_year AS start_year,
               d.end_year AS end_year, d.predecessor AS predecessor,
               d.description AS description
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_alias, {"names": names_list})
        return results[0] if results else None

    @staticmethod
    def merge_dynasty(dynasty: Dynasty) -> str:
        """智能合并政权/势力节点

        流程：
        1. 收集新势力的所有名字（name + aliases）
        2. 在 Neo4j 中查找是否有匹配
        3. 如果找到 → 合并别名、更新信息
        4. 如果没找到 → 创建新节点

        Returns:
            最终使用的 uid
        """
        all_names = dynasty.all_names()
        existing = GraphCRUD.find_dynasty_by_any_name(all_names)

        if existing:
            # ────── 合并到已有节点 ──────
            existing_uid = existing["uid"]
            existing_aliases = set(existing.get("aliases") or [])
            existing_aliases.add(existing.get("name", ""))

            new_aliases = existing_aliases | all_names
            final_name = existing.get("name")
            new_aliases.discard(final_name)
            new_aliases.discard("")

            # 合并 description（取更长的）
            existing_desc = existing.get("description") or ""
            new_desc = dynasty.description or ""
            final_desc = new_desc if len(new_desc) > len(existing_desc) else existing_desc

            update_cypher = """
            MATCH (d:Dynasty {uid: $uid})
            SET d.aliases = $aliases,
                d.description = $desc
            """
            params = {
                "uid": existing_uid,
                "aliases": sorted(new_aliases),
                "desc": final_desc,
            }

            # 补充缺失字段
            if dynasty.founder and not existing.get("founder"):
                update_cypher += ", d.founder = $founder"
                params["founder"] = dynasty.founder
            if dynasty.capital and not existing.get("capital"):
                update_cypher += ", d.capital = $capital"
                params["capital"] = dynasty.capital
            if dynasty.start_year is not None and existing.get("start_year") is None:
                update_cypher += ", d.start_year = $start_year"
                params["start_year"] = dynasty.start_year
            if dynasty.end_year is not None and existing.get("end_year") is None:
                update_cypher += ", d.end_year = $end_year"
                params["end_year"] = dynasty.end_year
            if dynasty.predecessor and not existing.get("predecessor"):
                update_cypher += ", d.predecessor = $predecessor"
                params["predecessor"] = dynasty.predecessor
            if dynasty.faction_type and dynasty.faction_type != "其他" and existing.get("faction_type") in (None, "其他", ""):
                update_cypher += ", d.faction_type = $faction_type"
                params["faction_type"] = dynasty.faction_type

            neo4j_conn.run_write(update_cypher, params)
            logger.debug(
                f"合并势力 [{dynasty.name}] → 已有节点 [{final_name}] "
                f"(uid={existing_uid}), 新别名: {new_aliases - existing_aliases}"
            )
            return existing_uid
        else:
            # ────── 创建新节点 ──────
            cypher = """
            MERGE (d:Dynasty {uid: $uid})
            SET d += $props
            """
            props = dynasty.neo4j_properties()
            uid = props.pop("uid")
            neo4j_conn.run_write(cypher, {"uid": uid, "props": props})
            logger.debug(
                f"新建势力 [{dynasty.name}] (uid={uid}, 类型={dynasty.faction_type}), "
                f"别名: {dynasty.aliases}"
            )
            return uid

    @staticmethod
    def upsert_dynasty(dynasty: Dynasty):
        """创建或更新政权节点（简单模式，仅按 uid 合并）"""
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

    # ━━━━━━━━━━━━━━━ 官职 CRUD ━━━━━━━━━━━━━━━

    @staticmethod
    def find_title_by_any_name(names: set[str]) -> dict | None:
        """通过任意名字（name 或 aliases）查找已有官职节点

        Args:
            names: 要查找的名字集合

        Returns:
            匹配到的官职记录 dict，或 None
        """
        if not names:
            return None
        names_list = sorted(n for n in names if n)
        if not names_list:
            return None

        # 优先用 name 精确匹配
        cypher_exact = """
        MATCH (t:OfficialTitle)
        WHERE t.name IN $names
        RETURN t.uid AS uid, t.name AS name, t.aliases AS aliases,
               t.category AS category, t.rank AS rank,
               t.description AS description, t.source AS source
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_exact, {"names": names_list})
        if results:
            return results[0]

        # 再用 aliases 匹配
        cypher_alias = """
        MATCH (t:OfficialTitle)
        WHERE any(alias IN t.aliases WHERE alias IN $names)
        RETURN t.uid AS uid, t.name AS name, t.aliases AS aliases,
               t.category AS category, t.rank AS rank,
               t.description AS description, t.source AS source
        LIMIT 1
        """
        results = neo4j_conn.run_query(cypher_alias, {"names": names_list})
        return results[0] if results else None

    @staticmethod
    def merge_official_title(title: OfficialTitle) -> str:
        """智能合并官职节点（尊重种子数据权威性）

        核心原则：
        - 种子数据（source='seed'）的 name、category、rank、description、duties
          拥有最高优先级，**不可被 LLM 提取覆盖**
        - LLM 提取的官职只能：
          ① 匹配到已有种子官职 → 追加别名（仅追加，不改其他）
          ② 匹配到已有 LLM 官职 → 正常合并
          ③ 完全新官职 → 创建新节点

        流程：
        1. 收集新官职的所有名字（name + aliases）
        2. 在 Neo4j 中查找是否有匹配
        3. 如果匹配到种子节点 → 只追加别名
        4. 如果匹配到 LLM 节点 → 合并别名 + 更新信息
        5. 如果没找到 → 创建新节点

        Returns:
            最终使用的 uid
        """
        all_names = title.all_names()
        existing = GraphCRUD.find_title_by_any_name(all_names)

        if existing:
            existing_uid = existing["uid"]
            existing_source = existing.get("source") or "llm"
            existing_aliases = set(existing.get("aliases") or [])
            existing_aliases.add(existing.get("name", ""))
            final_name = existing.get("name")

            if existing_source == "seed":
                # ────── 种子数据：只追加别名，绝不改其他字段 ──────
                new_aliases = existing_aliases | all_names
                new_aliases.discard(final_name)
                new_aliases.discard("")

                added = new_aliases - existing_aliases
                if added:
                    update_cypher = """
                    MATCH (t:OfficialTitle {uid: $uid})
                    SET t.aliases = $aliases
                    """
                    neo4j_conn.run_write(update_cypher, {
                        "uid": existing_uid,
                        "aliases": sorted(new_aliases),
                    })
                    logger.debug(
                        f"种子官职 [{final_name}] 追加别名: {added} "
                        f"(核心字段受保护，不可修改)"
                    )
                else:
                    logger.debug(
                        f"种子官职 [{final_name}] 已存在，无新别名需追加"
                    )
                return existing_uid
            else:
                # ────── LLM 数据：正常合并 ──────
                new_aliases = existing_aliases | all_names
                new_aliases.discard(final_name)
                new_aliases.discard("")

                # 合并 description（取更长的）
                existing_desc = existing.get("description") or ""
                new_desc = title.description or ""
                final_desc = new_desc if len(new_desc) > len(existing_desc) else existing_desc

                update_cypher = """
                MATCH (t:OfficialTitle {uid: $uid})
                SET t.aliases = $aliases,
                    t.description = $desc
                """
                params = {
                    "uid": existing_uid,
                    "aliases": sorted(new_aliases),
                    "desc": final_desc,
                }

                # 补充缺失字段
                if title.rank and not existing.get("rank"):
                    update_cypher += ", t.rank = $rank"
                    params["rank"] = title.rank
                if title.category and title.category != "其他" and existing.get("category") in (None, "其他", ""):
                    update_cypher += ", t.category = $category"
                    params["category"] = title.category

                neo4j_conn.run_write(update_cypher, params)
                logger.debug(
                    f"合并官职 [{title.name}] → 已有节点 [{final_name}] "
                    f"(uid={existing_uid}), 新别名: {new_aliases - existing_aliases}"
                )
                return existing_uid
        else:
            # ────── 创建新节点 ──────
            cypher = """
            MERGE (t:OfficialTitle {uid: $uid})
            SET t += $props
            """
            props = title.neo4j_properties()
            uid = props.pop("uid")
            neo4j_conn.run_write(cypher, {"uid": uid, "props": props})
            logger.debug(
                f"新建官职 [{title.name}] (uid={uid}, 类别={title.category}, "
                f"来源={title.source}), 别名: {title.aliases}"
            )
            return uid

    @staticmethod
    def upsert_official_title(title: OfficialTitle):
        """创建或更新官职节点（简单模式，仅按 uid 合并）"""
        cypher = """
        MERGE (t:OfficialTitle {uid: $uid})
        SET t += $props
        """
        props = title.neo4j_properties()
        uid = props.pop("uid")
        neo4j_conn.run_write(cypher, {"uid": uid, "props": props})

    @staticmethod
    def seed_official_titles():
        """将种子官职批量写入 Neo4j（幂等操作）

        种子官职享有最高优先级：
        - 如果节点不存在 → 创建
        - 如果节点已存在但 source!='seed' → 升级为 seed 并覆盖核心字段
        - 如果节点已存在且 source='seed' → 只补充缺失字段
        同时创建官职层级关系 SUPERVISES（上级→下级）。
        """
        from data.seed.seed_titles import SEED_OFFICIAL_TITLES

        logger.info(f"写入 {len(SEED_OFFICIAL_TITLES)} 个种子官职...")
        for title in SEED_OFFICIAL_TITLES:
            # 使用 MERGE + SET 确保种子数据始终覆盖
            cypher = """
            MERGE (t:OfficialTitle {uid: $uid})
            SET t.name = $name,
                t.aliases = $aliases,
                t.category = $category,
                t.description = $description,
                t.source = 'seed'
            """
            params = {
                "uid": title.uid,
                "name": title.name,
                "aliases": title.aliases,
                "category": title.category,
                "description": title.description,
            }
            if title.rank:
                cypher += ", t.rank = $rank"
                params["rank"] = title.rank
            if title.duties:
                cypher += ", t.duties = $duties"
                params["duties"] = title.duties
            if title.parent_title_uid:
                cypher += ", t.parent_title_uid = $parent_uid"
                params["parent_uid"] = title.parent_title_uid

            neo4j_conn.run_write(cypher, params)

        # 创建层级关系 SUPERVISES（上级 → 下级）
        for title in SEED_OFFICIAL_TITLES:
            if title.parent_title_uid:
                rel_cypher = """
                MATCH (parent:OfficialTitle {uid: $parent_uid})
                MATCH (child:OfficialTitle {uid: $child_uid})
                MERGE (parent)-[:SUPERVISES]->(child)
                """
                neo4j_conn.run_write(rel_cypher, {
                    "parent_uid": title.parent_title_uid,
                    "child_uid": title.uid,
                })

        logger.info(f"种子官职写入完成，共 {len(SEED_OFFICIAL_TITLES)} 个")

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
            logger.debug(f"关系跳过：找不到源实体 [{relation.source}]（可能是藩镇/机构等非实体名称）")
            return False
        if not target_uid:
            logger.debug(f"关系跳过：找不到目标实体 [{relation.target}]（可能是藩镇/机构等非实体名称）")
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
    def get_dynasty_by_name(name: str) -> list[dict]:
        """通过名字或别名查找政权/势力"""
        cypher = """
        MATCH (d:Dynasty)
        WHERE d.name = $name OR $name IN d.aliases
        RETURN d.uid AS uid, d.name AS name, d.aliases AS aliases,
               d.faction_type AS faction_type, d.founder AS founder,
               d.capital AS capital, d.description AS description
        """
        return neo4j_conn.run_query(cypher, {"name": name})

    @staticmethod
    def get_all_dynasty_names() -> list[dict]:
        """获取所有政权/势力的名字和别名（用于向 LLM 提供上下文）"""
        cypher = """
        MATCH (d:Dynasty)
        RETURN d.name AS name, d.aliases AS aliases, d.faction_type AS faction_type
        ORDER BY d.name
        """
        return neo4j_conn.run_query(cypher)

    @staticmethod
    def get_graph_stats() -> dict:
        """获取图谱统计信息"""
        stats = {}
        for label in ["Person", "Dynasty", "Event", "Place", "OfficialTitle"]:
            result = neo4j_conn.run_query(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            stats[label] = result[0]["cnt"] if result else 0
        # 按 faction_type 细分 Dynasty
        faction_result = neo4j_conn.run_query(
            "MATCH (d:Dynasty) RETURN d.faction_type AS ft, count(d) AS cnt ORDER BY ft"
        )
        if faction_result:
            stats["Dynasty_detail"] = {r["ft"] or "未分类": r["cnt"] for r in faction_result}
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

    @staticmethod
    def get_all_title_names() -> list[dict]:
        """获取所有官职的名字和别名（用于向 LLM 提供上下文）"""
        cypher = """
        MATCH (t:OfficialTitle)
        RETURN t.name AS name, t.aliases AS aliases, t.category AS category,
               t.source AS source
        ORDER BY t.source DESC, t.name
        """
        return neo4j_conn.run_query(cypher)

    # ━━━━━━━━━━━━━━━ 编辑操作：删除/更新 ━━━━━━━━━━━━━━━

    @staticmethod
    def delete_node(uid: str) -> bool:
        """删除节点及其所有关系（适用于任意类型的节点）

        Args:
            uid: 节点的唯一标识符

        Returns:
            是否成功删除
        """
        # 先确认节点存在
        check = neo4j_conn.run_query(
            "MATCH (n {uid: $uid}) RETURN n.uid AS uid, labels(n) AS labels", {"uid": uid}
        )
        if not check:
            logger.warning(f"删除节点失败：节点不存在 [{uid}]")
            return False

        # DETACH DELETE 会同时删除节点及其所有关系
        neo4j_conn.run_write("MATCH (n {uid: $uid}) DETACH DELETE n", {"uid": uid})
        labels = check[0].get("labels", [])
        logger.info(f"已删除节点 [{uid}] (类型: {labels})")
        return True

    @staticmethod
    def delete_relation(source_uid: str, target_uid: str, rel_type: str) -> bool:
        """删除两个节点之间的指定类型关系

        Args:
            source_uid: 源节点 uid
            target_uid: 目标节点 uid
            rel_type: 关系类型（英文大写，如 FATHER_OF）

        Returns:
            是否成功删除
        """
        # 清洗关系类型
        safe_type = "".join(c if c.isalnum() or c == "_" else "_" for c in rel_type)
        if not safe_type:
            return False

        cypher = f"""
        MATCH (a {{uid: $source_uid}})-[r:{safe_type}]->(b {{uid: $target_uid}})
        DELETE r
        RETURN count(r) AS cnt
        """
        try:
            neo4j_conn.run_write(cypher, {"source_uid": source_uid, "target_uid": target_uid})
            logger.info(f"已删除关系 [{source_uid}]-[{safe_type}]->[{target_uid}]")
            return True
        except Exception as e:
            logger.warning(f"删除关系失败: {e}")
            return False

    @staticmethod
    def add_relation(source_uid: str, target_uid: str, rel_type: str, description: str = "") -> bool:
        """在两个已有节点之间新增关系

        Args:
            source_uid: 源节点 uid
            target_uid: 目标节点 uid
            rel_type: 关系类型（英文大写）
            description: 关系描述

        Returns:
            是否成功
        """
        safe_type = rel_type.upper().replace(" ", "_").replace("-", "_")
        safe_type = "".join(c if c.isalnum() or c == "_" else "_" for c in safe_type)
        if not safe_type:
            safe_type = "RELATED_TO"

        cypher = f"""
        MATCH (a {{uid: $source_uid}})
        MATCH (b {{uid: $target_uid}})
        MERGE (a)-[r:{safe_type}]->(b)
        SET r.description = $desc
        """
        try:
            neo4j_conn.run_write(cypher, {
                "source_uid": source_uid,
                "target_uid": target_uid,
                "desc": description,
            })
            logger.info(f"已新增关系 [{source_uid}]-[{safe_type}]->[{target_uid}]")
            return True
        except Exception as e:
            logger.warning(f"新增关系失败: {e}")
            return False

    @staticmethod
    def update_relation(source_uid: str, target_uid: str, old_rel_type: str, new_rel_type: str, description: str = "") -> bool:
        """修改两个节点之间的关系类型（先删旧关系，再建新关系）

        Args:
            source_uid: 源节点 uid
            target_uid: 目标节点 uid
            old_rel_type: 原关系类型
            new_rel_type: 新关系类型
            description: 新关系描述

        Returns:
            是否成功
        """
        # 先获取旧关系的属性
        old_safe = "".join(c if c.isalnum() or c == "_" else "_" for c in old_rel_type)
        if not old_safe:
            return False

        # 删除旧关系
        deleted = GraphCRUD.delete_relation(source_uid, target_uid, old_rel_type)
        if not deleted:
            # 尝试反方向
            deleted = GraphCRUD.delete_relation(target_uid, source_uid, old_rel_type)

        # 创建新关系
        added = GraphCRUD.add_relation(source_uid, target_uid, new_rel_type, description)
        logger.info(f"已修改关系 [{source_uid}]-[{old_rel_type}→{new_rel_type}]->[{target_uid}]")
        return added

    @staticmethod
    def update_person_aliases(person_uid: str, aliases: list[str]) -> bool:
        """更新人物的别名列表

        Args:
            person_uid: 人物 uid
            aliases: 新的别名列表

        Returns:
            是否成功
        """
        # 去除空字符串和重复
        clean_aliases = sorted(set(a.strip() for a in aliases if a.strip()))

        cypher = """
        MATCH (p:Person {uid: $uid})
        SET p.aliases = $aliases
        RETURN p.uid AS uid
        """
        results = neo4j_conn.run_query(cypher, {"uid": person_uid})
        if not results:
            # run_query 可能不适用于写操作，改用 run_write
            neo4j_conn.run_write(
                "MATCH (p:Person {uid: $uid}) SET p.aliases = $aliases",
                {"uid": person_uid, "aliases": clean_aliases}
            )
        else:
            neo4j_conn.run_write(
                "MATCH (p:Person {uid: $uid}) SET p.aliases = $aliases",
                {"uid": person_uid, "aliases": clean_aliases}
            )
        logger.info(f"已更新人物别名 [{person_uid}]: {clean_aliases}")
        return True

    @staticmethod
    def get_all_relation_types() -> list[str]:
        """获取图谱中所有已使用的关系类型"""
        cypher = """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN relationshipType
        ORDER BY relationshipType
        """
        results = neo4j_conn.run_query(cypher)
        return [r["relationshipType"] for r in results]


graph_crud = GraphCRUD()

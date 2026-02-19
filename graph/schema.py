"""Neo4j Schema 初始化与约束管理"""

from loguru import logger

from graph.connection import neo4j_conn


def init_constraints():
    """创建唯一性约束和索引"""
    constraints = [
        # 唯一性约束
        "CREATE CONSTRAINT person_uid IF NOT EXISTS FOR (p:Person) REQUIRE p.uid IS UNIQUE",
        "CREATE CONSTRAINT dynasty_uid IF NOT EXISTS FOR (d:Dynasty) REQUIRE d.uid IS UNIQUE",
        "CREATE CONSTRAINT event_uid IF NOT EXISTS FOR (e:Event) REQUIRE e.uid IS UNIQUE",
        "CREATE CONSTRAINT place_uid IF NOT EXISTS FOR (pl:Place) REQUIRE pl.uid IS UNIQUE",
        # 人物名字索引（用于按名字/别名查找，核心功能）
        "CREATE INDEX person_name_idx IF NOT EXISTS FOR (p:Person) ON (p.original_name)",
        # 全文索引 - 用于模糊搜索人名和别名
        """CREATE FULLTEXT INDEX person_fulltext_index IF NOT EXISTS
           FOR (p:Person) ON EACH [p.original_name, p.description]""",
        """CREATE FULLTEXT INDEX event_fulltext_index IF NOT EXISTS
           FOR (e:Event) ON EACH [e.name, e.description]""",
    ]

    for cypher in constraints:
        try:
            neo4j_conn.run_write(cypher)
            logger.debug(f"执行约束: {cypher[:60]}...")
        except Exception as e:
            logger.warning(f"约束可能已存在: {e}")

    logger.info("Neo4j Schema 约束与索引初始化完成")


def clear_database():
    """清空整个数据库（危险操作，仅用于开发）"""
    neo4j_conn.run_write("MATCH (n) DETACH DELETE n")
    logger.warning("⚠ 数据库已清空")

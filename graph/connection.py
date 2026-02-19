"""Neo4j 图数据库连接与操作管理"""

from contextlib import contextmanager

from loguru import logger
from neo4j import GraphDatabase, Session

from config.settings import settings


class Neo4jConnection:
    """Neo4j 数据库连接管理器"""

    def __init__(self):
        self._driver = None

    def connect(self):
        """建立数据库连接"""
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
            # 验证连接
            self._driver.verify_connectivity()
            logger.info(f"已连接 Neo4j: {settings.NEO4J_URI}")

    def close(self):
        """关闭连接"""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j 连接已关闭")

    @contextmanager
    def session(self) -> Session:
        """获取数据库会话的上下文管理器"""
        if self._driver is None:
            self.connect()
        session = self._driver.session()
        try:
            yield session
        finally:
            session.close()

    def run_query(self, query: str, parameters: dict | None = None) -> list[dict]:
        """执行 Cypher 查询并返回结果"""
        with self.session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def run_write(self, query: str, parameters: dict | None = None):
        """执行写入 Cypher 查询"""
        with self.session() as session:
            session.run(query, parameters or {})


# 全局连接实例
neo4j_conn = Neo4jConnection()

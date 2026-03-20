"""全局配置管理"""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置，从 .env 文件和环境变量加载"""

    # 项目路径
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    # LLM (维纳斯平台 - 用于 Cypher 生成等内部任务)
    LLM_API_BASE: str = "http://v2.open.venus.oa.com/llmproxy"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "deepseek-v3.2"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 8192

    # QA LLM (维纳斯平台 - 用于知识库问答 / AI 总结，面向用户)
    QA_LLM_API_BASE: str = "http://v2.open.venus.oa.com/llmproxy"
    QA_LLM_API_KEY: str = ""  # 默认复用 LLM_API_KEY，由 llm_client 处理
    QA_LLM_MODEL: str = "glm-5"

    # Embedding
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-zh-v1.5"

    # 数据源
    BOOKS_DIR: str = "books"

    # 服务
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # 数据路径
    @property
    def RAW_DATA_DIR(self) -> Path:
        return self.PROJECT_ROOT / self.BOOKS_DIR

    @property
    def PROCESSED_DATA_DIR(self) -> Path:
        return self.PROJECT_ROOT / "data" / "processed"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

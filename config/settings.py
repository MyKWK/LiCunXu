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

    # LLM (维纳斯平台 - 用于数据摄入/清洗等内部任务)
    LLM_API_BASE: str = "http://v2.open.venus.oa.com/llmproxy"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "qwen3-vl-235b-a22b-thinking"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 8192

    # DeepSeek (用于知识库问答)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

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

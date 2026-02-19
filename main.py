"""应用入口"""

import sys
import uvicorn
from loguru import logger

from config.settings import settings


def main():
    """启动 FastAPI 服务"""
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add("logs/app.log", rotation="10 MB", retention="7 days", level="DEBUG")

    logger.info("=" * 50)
    logger.info("五代历史知识图谱与智能问答系统启动中...")
    logger.info(f"Neo4j: {settings.NEO4J_URI}")
    logger.info(f"LLM: {settings.LLM_MODEL_NAME}")
    logger.info(f"服务地址: http://{settings.APP_HOST}:{settings.APP_PORT}")
    logger.info("=" * 50)

    uvicorn.run(
        "api.routes:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()

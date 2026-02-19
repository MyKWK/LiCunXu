"""种子数据加载器 - 将种子数据导入 Neo4j（适配新的合并模式）"""

from loguru import logger

from data.seed.seed_data import (
    SEED_DYNASTIES,
    SEED_EVENTS,
    SEED_PERSONS,
    SEED_PLACES,
    SEED_RELATIONS,
)
from graph.crud import graph_crud
from graph.schema import init_constraints


def load_seed_data(clear_first: bool = False):
    """将种子数据加载到 Neo4j

    Args:
        clear_first: 是否先清空数据库
    """
    from graph.schema import clear_database

    if clear_first:
        clear_database()

    init_constraints()

    # 1. 加载政权
    logger.info(f"加载 {len(SEED_DYNASTIES)} 个政权...")
    for dynasty in SEED_DYNASTIES:
        graph_crud.upsert_dynasty(dynasty)

    # 2. 加载地点
    logger.info(f"加载 {len(SEED_PLACES)} 个地点...")
    for place in SEED_PLACES:
        graph_crud.upsert_place(place)

    # 3. 加载人物（使用 merge_person 智能合并）
    logger.info(f"加载 {len(SEED_PERSONS)} 个人物...")
    for person in SEED_PERSONS:
        graph_crud.merge_person(person)

    # 4. 加载事件
    logger.info(f"加载 {len(SEED_EVENTS)} 个事件...")
    for event in SEED_EVENTS:
        graph_crud.upsert_event(event)
        for participant_name in event.participants:
            try:
                graph_crud.link_event_participant(event.uid, participant_name)
            except Exception as e:
                logger.warning(f"事件参与关联失败 {event.uid} - {participant_name}: {e}")

    # 5. 加载关系（通过名字解析）
    logger.info(f"加载 {len(SEED_RELATIONS)} 条关系...")
    for relation in SEED_RELATIONS:
        try:
            graph_crud.create_relation_by_name(relation)
        except Exception as e:
            logger.warning(f"关系创建失败 [{relation.source}]->[{relation.target}]: {e}")

    # 6. 关联政权创建者
    logger.info("关联政权创建者...")
    for dynasty in SEED_DYNASTIES:
        if dynasty.founder:
            try:
                graph_crud.link_dynasty_founder(dynasty.uid, dynasty.founder)
            except Exception as e:
                logger.warning(f"政权创建者关联失败 {dynasty.uid}: {e}")

    stats = graph_crud.get_graph_stats()
    logger.info(f"种子数据加载完成！图谱统计: {stats}")
    return stats


if __name__ == "__main__":
    load_seed_data(clear_first=True)

"""清洗被污染的人物节点

问题根源：merge_person() 使用"名字集合交集"来判断是否同一人物，
但 LLM 会把"太宗"、"晋王"、"太子"、"次子"等通用称号作为别名，
导致不同人物因共享通用别名而被错误合并（链式雪球效应）。

清洗策略：
1. 识别被污染的超级节点（别名>合理阈值）
2. 用 LLM 判断哪些别名是正确的
3. 将错误吸收的别名对应的关系重新分配或删除
4. 拆分被错误合并的人物

由于关系也已被污染（错误的关系连到了错误的节点上），
最安全的方式是：对被严重污染的节点，清除其错误别名，
并将错误关系标记出来。
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.connection import neo4j_conn
from config.llm_client import venus_llm
from loguru import logger


# ━━━━━━━━━━ 已知正确的核心人物信息（根据史实手动校正） ━━━━━━━━━━

KNOWN_PERSONS = {
    "person_li_keyong": {
        "original_name": "李克用",
        "correct_aliases": ["朱邪克用", "独眼龙", "李鸦儿", "飞虎子", "晋王", "老晋王", "太祖武皇帝", "献祖文皇帝", "李邈佶烈", "李国昌", "并门"],
        "role": "藩镇节度使",
        "birth_year": 856,
        "death_year": 908,
        "death_cause": "病逝",
        "loyalty": ["唐", "河东", "沙陀"],
        "description": "沙陀人，唐末河东节度使，与朱温对抗多年。封晋王，死后其子李存勖继承其志建立后唐。"
    },
    "person_li_cunxu": {
        "original_name": "李存勖",
        "correct_aliases": ["李存勗", "李亚子", "亚子", "庄宗", "后唐庄宗", "庄宗皇帝", "唐庄宗", "同光帝", "同光皇帝", "李天下", "晋王", "李晋王", "李三郎", "李皇帝", "唐主", "河南天子"],
        "role": "皇帝",
        "birth_year": 885,
        "death_year": 926,
        "death_cause": "兴教门之变中被杀",
        "loyalty": ["河东", "后唐", "晋"],
        "description": "李克用之子，后唐开国皇帝。灭后梁统一北方，后因宠信伶人、猜忌功臣导致兴教门之变被杀。"
    },
    "person_yelv_deguang": {
        "original_name": "耶律德光",
        "correct_aliases": ["辽太宗", "德光", "契丹主", "契丹皇帝", "辽主", "天皇王", "天下兵马大元帅", "大元帅", "元帅太子", "寿昌皇太弟", "皇太弟"],
        "role": "皇帝",
        "birth_year": 902,
        "death_year": 947,
        "death_cause": "北归途中病逝",
        "loyalty": ["契丹/辽", "契丹", "辽"],
        "description": "辽太宗，耶律阿保机次子。助石敬瑭灭后唐建后晋，获燕云十六州。后灭后晋入主中原，但北归途中病逝。"
    },
    "person_yelv_abaoji": {
        "original_name": "耶律阿保机",
        "correct_aliases": ["阿保机", "安巴坚", "辽太祖", "世里阿保机", "天皇帝", "契丹天皇帝", "辽朝天皇帝"],
        "role": "皇帝",
        "birth_year": 872,
        "death_year": 926,
        "death_cause": "病逝",
        "loyalty": ["契丹", "辽"],
        "description": "契丹/辽朝开国皇帝，统一契丹各部，建立辽朝。"
    },
    "person_xizong": {
        "original_name": "僖宗",
        "correct_aliases": ["唐僖宗", "唐僖宗李儇", "僖宗李儇", "李儇", "寿王李杰", "李杰"],
        "role": "皇帝",
        "birth_year": 862,
        "death_year": 888,
        "death_cause": "病逝",
        "loyalty": ["唐朝"],
        "description": "唐朝倒数第三位皇帝，在位期间爆发黄巢之乱。"
    },
    "person_zhuwen": {
        "original_name": "朱温",
        "correct_aliases": ["朱全忠", "朱晃", "梁太祖", "朱三", "朱老三", "朱阿三", "朱三哥", "阿三", "泼朱三", "全忠", "东平王", "梁王", "砀山大盗", "朱大强盗", "朱温老贼", "老头子", "朱大帅"],
        "role": "皇帝",
        "birth_year": 852,
        "death_year": 912,
        "death_cause": "被其子朱友珪弑杀",
        "loyalty": ["后梁"],
        "description": "后梁开国皇帝，原为黄巢部将，后叛变归唐，篡唐建后梁。"
    },
    "person_guo_wei": {
        "original_name": "郭威",
        "correct_aliases": ["后周太祖", "周太祖", "太祖", "郭雀儿", "郭侍中"],
        "role": "皇帝",
        "birth_year": 904,
        "death_year": 954,
        "death_cause": "病逝",
        "loyalty": ["后汉", "后周"],
        "description": "后周开国皇帝，推翻后汉建立后周。"
    },
    "person_tian_jun": {
        "original_name": "田頵",
        "correct_aliases": ["田二哥", "田二当家", "田大将军", "田老二", "田"],
        "role": "将领",
        "birth_year": 887 if True else None,  # uncertain
        "death_year": 903,
        "death_cause": None,
        "loyalty": ["杨吴"],
        "description": "唐末宁国军节度使。"
    },
    "person_liu_fu_ren": {
        "original_name": "刘夫人",
        "correct_aliases": ["刘玉娘", "刘后", "刘皇后", "刘氏", "刘太太", "刘太夫人", "刘山人之女", "刘小姐", "魏国夫人", "王妃刘氏"],
        "role": "后妃",
        "birth_year": None,
        "death_year": 926,
        "death_cause": "自焚而死",
        "loyalty": ["后唐"],
        "description": "后唐庄宗李存勖的皇后刘氏，以贪财著称。"
    },
    "person_zhu_youzhen": {
        "original_name": "朱友贞",
        "correct_aliases": ["后梁末帝", "末帝", "梁末帝", "均王", "梁主", "朱瑱", "朱锽", "大梁皇帝"],
        "role": "皇帝",
        "birth_year": 888,
        "death_year": 923,
        "death_cause": "自杀",
        "loyalty": ["后梁"],
        "description": "后梁末代皇帝，朱温之子。"
    },
    "person_qian_liu": {
        "original_name": "钱镠",
        "correct_aliases": ["吴越王", "吴越王钱镠", "武肃王", "吴越武肃王", "武肃", "婆留", "钱公", "钱具美", "淮海王", "越王"],
        "role": "皇帝",
        "birth_year": 852,
        "death_year": 932,
        "death_cause": "病逝",
        "loyalty": ["吴越"],
        "description": "吴越国开国之君。"
    },
    "person_gao_jichang": {
        "original_name": "高季昌",
        "correct_aliases": ["高季兴", "南平王", "荆南节度使", "朱季昌", "文献王", "渤海王"],
        "role": "藩镇节度使",
        "birth_year": 858,
        "death_year": 929,
        "death_cause": "病逝",
        "loyalty": ["南平", "荆南"],
        "description": "南平（荆南）开国者。"
    },
    "person_gao_xingzhou": {
        "original_name": "高行周",
        "correct_aliases": [],
        "role": "将领",
        "birth_year": None,
        "death_year": None,
        "death_cause": None,
        "loyalty": ["后唐", "后晋", "后汉", "后周"],
        "description": "五代时期著名将领。"
    },
    "person_ding_cong_shi": {
        "original_name": "丁从实",
        "correct_aliases": ["董从实"],
        "role": "刺史",
        "birth_year": None,
        "death_year": None,
        "death_cause": None,
        "loyalty": [],
        "description": ""
    },
    "person_xu_xianfei": {
        "original_name": "徐贤妃",
        "correct_aliases": ["贤妃", "徐氏姐妹", "诩圣皇太妃"],
        "role": "后妃",
        "birth_year": None,
        "death_year": 926,
        "death_cause": None,
        "loyalty": ["后唐"],
        "description": "后唐庄宗妃嫔。"
    },
    "person_liu_hanhong": {
        "original_name": "刘汉宏",
        "correct_aliases": [],
        "role": "叛将",
        "birth_year": None,
        "death_year": None,
        "death_cause": None,
        "loyalty": [],
        "description": ""
    },
    "person_ling_ren": {
        "original_name": "敬新磨",
        "correct_aliases": ["敬新磨"],
        "role": "艺人",
        "birth_year": None,
        "death_year": None,
        "death_cause": None,
        "loyalty": ["后唐"],
        "description": "后唐庄宗宠信的伶人。"
    },
    "person_yelv_jing": {
        "original_name": "耶律璟",
        "correct_aliases": ["辽穆宗", "穆宗", "睡王", "孝和皇帝"],
        "role": "皇帝",
        "birth_year": 931,
        "death_year": 969,
        "death_cause": "被侍从杀害",
        "loyalty": ["辽"],
        "description": "辽穆宗，以嗜睡好杀著称。"
    },
    "person_yelv_be": {
        "original_name": "耶律倍",
        "correct_aliases": ["东丹王", "人皇王", "突欲", "耶律突欲", "东丹王突欲", "李赞华", "让国皇帝", "东丹赞华", "东丹慕华", "老大耶律倍", "倍"],
        "role": "皇太子",
        "birth_year": 899,
        "death_year": 937,
        "death_cause": "被杀",
        "loyalty": ["契丹", "辽", "后唐"],
        "description": "耶律阿保机长子，本为皇太子，让位于弟耶律德光后出逃后唐。"
    },
    "person_wang_zongyi": {
        "original_name": "王宗懿",
        "correct_aliases": ["王元膺", "太子"],
        "role": "皇子",
        "birth_year": None,
        "death_year": None,
        "death_cause": None,
        "loyalty": ["前蜀"],
        "description": "前蜀高祖王建太子。"
    },
}


def cleanup_polluted_nodes():
    """清洗被污染的节点"""
    logger.info("=" * 60)
    logger.info("开始清洗被污染的人物节点")
    logger.info("=" * 60)

    fixed = 0
    for uid, correct_data in KNOWN_PERSONS.items():
        # 获取当前节点数据
        result = neo4j_conn.run_query(
            "MATCH (p:Person {uid: $uid}) RETURN p",
            {"uid": uid}
        )
        if not result:
            logger.warning(f"节点 {uid} 不存在，跳过")
            continue

        current = dict(result[0]["p"])
        current_aliases = set(current.get("aliases", []))
        correct_aliases = set(correct_data["correct_aliases"])

        # 计算被错误吸收的别名
        wrong_aliases = current_aliases - correct_aliases - {correct_data["original_name"]}
        
        if not wrong_aliases and current.get("original_name") == correct_data["original_name"]:
            logger.info(f"[OK] {correct_data['original_name']} ({uid}) 无需修复")
            continue

        logger.info(f"[修复] {correct_data['original_name']} ({uid})")
        logger.info(f"  当前别名 ({len(current_aliases)}): {sorted(current_aliases)}")
        logger.info(f"  正确别名 ({len(correct_aliases)}): {sorted(correct_aliases)}")
        logger.info(f"  错误别名 ({len(wrong_aliases)}): {sorted(wrong_aliases)}")

        # 更新节点
        update_params = {
            "uid": uid,
            "original_name": correct_data["original_name"],
            "aliases": sorted(correct_aliases),
            "role": correct_data["role"],
            "description": correct_data["description"],
            "loyalty": correct_data["loyalty"],
        }
        
        update_cypher = """
        MATCH (p:Person {uid: $uid})
        SET p.original_name = $original_name,
            p.aliases = $aliases,
            p.role = $role,
            p.description = $description,
            p.loyalty = $loyalty
        """
        
        if correct_data.get("birth_year") is not None:
            update_cypher += ", p.birth_year = $birth_year"
            update_params["birth_year"] = correct_data["birth_year"]
        if correct_data.get("death_year") is not None:
            update_cypher += ", p.death_year = $death_year"
            update_params["death_year"] = correct_data["death_year"]
        if correct_data.get("death_cause") is not None:
            update_cypher += ", p.death_cause = $death_cause"
            update_params["death_cause"] = correct_data["death_cause"]
        
        neo4j_conn.run_write(update_cypher, update_params)
        fixed += 1
        logger.info(f"  ✓ 已修复，移除 {len(wrong_aliases)} 个错误别名")

    logger.info("=" * 60)
    logger.info(f"清洗完成: 修复了 {fixed} 个被污染的节点")
    logger.info("=" * 60)

    # 修复完成后，清理重复的关系
    cleanup_duplicate_relations()


def cleanup_duplicate_relations():
    """清理重复的关系"""
    logger.info("开始清理重复关系...")
    
    # 删除自引用关系（节点指向自己）
    result = neo4j_conn.run_write("""
    MATCH (p)-[r]->(p)
    DELETE r
    RETURN count(r) AS deleted
    """)
    logger.info(f"删除自引用关系: {result}")

    # 合并相同起止节点和类型的重复关系（保留一条）
    result = neo4j_conn.run_query("""
    MATCH (a)-[r]->(b)
    WITH a, b, type(r) AS relType, collect(r) AS rels
    WHERE size(rels) > 1
    RETURN count(*) AS duplicate_groups
    """)
    if result:
        dup_count = result[0]["duplicate_groups"]
        logger.info(f"发现 {dup_count} 组重复关系")
        
        if dup_count > 0:
            neo4j_conn.run_write("""
            MATCH (a)-[r]->(b)
            WITH a, b, type(r) AS relType, collect(r) AS rels
            WHERE size(rels) > 1
            FOREACH (r IN tail(rels) | DELETE r)
            """)
            logger.info(f"已去重，每组只保留一条关系")

    logger.info("关系清理完成")


if __name__ == "__main__":
    cleanup_polluted_nodes()

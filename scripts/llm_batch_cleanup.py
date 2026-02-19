"""LLM 批量清洗脚本 — 修复人物别名污染 + 事件关系错误

问题根源：
  merge_person() 使用"名字集合交集"判断同一人物，
  LLM 提取时把通用称号（太宗、晋王、太子）或同文出现的其他人名作为别名，
  导致不同人物因共享名字而被链式合并（雪球效应）。

  cleanup_polluted_nodes.py 只修复了 17 个核心人物的别名，
  但数据库中有 62+ 个人物别名被污染，且事件关系也因此错误。

修复策略：
  阶段一：别名清洗
    - 对别名数 >= 3 的人物，让 LLM 判断哪些别名确实属于该人物
    - 已经在 KNOWN_PERSONS 中手动修复过的跳过
    - 清洗后更新 Neo4j
  
  阶段二：事件关系修复
    - 基于事件 description 和 participants 字段，重建人物-事件关系
    - 移除已故人物的死后错误关系
    - 补建缺失的 PARTICIPATED_IN 关系
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.connection import neo4j_conn
from config.llm_client import venus_llm
from loguru import logger

# ━━━━━━━━ 配置 ━━━━━━━━

# 已手动修复过的人物 uid（跳过）
ALREADY_FIXED_UIDS = {
    "person_li_keyong", "person_li_cunxu", "person_yelv_deguang",
    "person_yelv_abaoji", "person_xizong", "person_zhuwen",
    "person_guo_wei", "person_tian_jun", "person_liu_fu_ren",
    "person_zhu_youzhen", "person_qian_liu", "person_gao_jichang",
    "person_gao_xingzhou", "person_ding_cong_shi", "person_xu_xianfei",
    "person_liu_hanhong", "person_ling_ren", "person_yelv_jing",
    "person_yelv_be", "person_wang_zongyi",
    # 上一轮修复事件关系时涉及的人物（别名已经正确）
    "person_li_siyuan", "person_shi_jingtang", "person_liu_zhiyuan",
    "person_chai_rong", "person_zhao_kuangyin",
}

# 追思类关键词（死后事件中保留这些）
MEMORIAL_KEYWORDS = ['追封', '追赠', '追谥', '祭', '葬', '陵', '安葬', '遗命', '遗诏', '庙号', '谥号']

# 进度文件
PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "cleanup_progress.json")

# 批次大小（每处理这么多人物保存一次进度）
BATCH_SAVE_INTERVAL = 10

# LLM 调用间隔（秒），避免过快触发限流
LLM_CALL_INTERVAL = 0.5


# ━━━━━━━━ 阶段一：别名清洗 ━━━━━━━━

ALIAS_CLEANUP_SYSTEM_PROMPT = """你是一个五代十国（公元 907-960 年）历史专家。

我会给你一个人物的信息（姓名、角色、所属势力、描述）和他当前的别名列表。
由于数据处理bug，别名列表中可能**混入了其他人物的名字**。

你的任务是：判断每个别名是否确实属于这个人物。

判断标准：
1. 这个名字确实是该人物的本名、字、号、赐名、封号、庙号、谥号、官职称呼、绰号、昵称
2. 同一个人的名字变体（如"朱全忠"是"朱温"的赐名）
3. 如果一个名字明显是另一个不同人物的名字，应该标记为错误

常见错误类型（必须标记为 wrong）：
- 子女、兄弟、父亲的名字被混入（如马殷的别名中出现"马希广"、"马希范"——这是他的儿子）
- 完全不同的人物名字被混入（如刘知远的别名中出现"张允"、"丹王李允"——这是不同的人）
- 官职称号不应作为别名，除非该人物以此称号著称（如"吏部侍郎"不是人名）
- 只有称号没有姓名的泛称不应作为别名（如"太后"、"皇帝"、"节度使"）

请以 JSON 格式返回，包含两个数组：
{
  "correct_aliases": ["确实属于该人物的别名1", "别名2", ...],
  "wrong_aliases": ["不属于该人物的错误别名1", ...]
}

只返回 JSON，不要解释。"""


def build_alias_check_prompt(name: str, aliases: list, role: str, 
                              loyalty: list, description: str,
                              birth_year=None, death_year=None) -> str:
    """构建别名检查的 user prompt"""
    info_parts = [f"人物：{name}"]
    if role:
        info_parts.append(f"角色：{role}")
    if loyalty:
        info_parts.append(f"所属势力：{'、'.join(loyalty)}")
    if birth_year:
        info_parts.append(f"出生年：{birth_year}")
    if death_year:
        info_parts.append(f"死亡年：{death_year}")
    if description:
        info_parts.append(f"描述：{description[:200]}")
    
    info_parts.append(f"\n当前别名列表（共{len(aliases)}个）：")
    for i, alias in enumerate(aliases, 1):
        info_parts.append(f"  {i}. {alias}")
    
    info_parts.append("\n请判断每个别名是否确实属于这个人物。")
    return "\n".join(info_parts)


def parse_llm_json(text: str) -> dict | None:
    """解析 LLM 返回的 JSON"""
    # 去掉 <think> 标签
    if "<think>" in text:
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    
    text = text.strip()
    
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 尝试提取 markdown 代码块
    if "```" in text:
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    
    # 尝试提取最外层花括号
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    
    return None


def load_progress() -> dict:
    """加载进度"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"phase1_done": [], "phase2_done": False, "alias_fixes": {}}


def save_progress(progress: dict):
    """保存进度"""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def get_persons_to_clean(min_aliases: int = 3, skip_fixed: bool = True) -> list[dict]:
    """获取需要清洗别名的人物列表
    
    Args:
        min_aliases: 最少别名数（低于此数的跳过）
        skip_fixed: 是否跳过 ALREADY_FIXED_UIDS 中的人物
    """
    results = neo4j_conn.run_query("""
    MATCH (p:Person)
    WHERE size(p.aliases) >= $min_aliases
    RETURN p.uid AS uid, p.original_name AS name, 
           p.aliases AS aliases, p.role AS role,
           p.loyalty AS loyalty, p.description AS description,
           p.birth_year AS birth_year, p.death_year AS death_year
    ORDER BY size(p.aliases) DESC
    """, {"min_aliases": min_aliases})
    
    if skip_fixed:
        return [r for r in results if r["uid"] not in ALREADY_FIXED_UIDS]
    return results


def clean_aliases_with_llm(person: dict) -> dict | None:
    """用 LLM 清洗单个人物的别名
    
    Returns:
        {"correct_aliases": [...], "wrong_aliases": [...]} 或 None
    """
    prompt = build_alias_check_prompt(
        name=person["name"],
        aliases=person["aliases"],
        role=person.get("role", ""),
        loyalty=person.get("loyalty", []),
        description=person.get("description", ""),
        birth_year=person.get("birth_year"),
        death_year=person.get("death_year"),
    )
    
    try:
        response = venus_llm.chat(
            messages=[
                {"role": "system", "content": ALIAS_CLEANUP_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.05,
            max_tokens=2048,
        )
        
        result = parse_llm_json(response)
        if result and "correct_aliases" in result:
            return result
        else:
            logger.warning(f"  LLM 返回格式异常: {response[:200]}")
            return None
    except Exception as e:
        logger.error(f"  LLM 调用失败: {e}")
        return None


def apply_alias_fix(uid: str, correct_aliases: list[str]):
    """将清洗后的别名写入 Neo4j"""
    neo4j_conn.run_write("""
    MATCH (p:Person {uid: $uid})
    SET p.aliases = $aliases
    """, {"uid": uid, "aliases": sorted(set(correct_aliases))})


def run_phase1_alias_cleanup(min_aliases: int = 3, skip_fixed: bool = True):
    """阶段一：批量清洗别名"""
    logger.info("=" * 70)
    logger.info(f"阶段一：LLM 批量别名清洗 (min_aliases={min_aliases}, skip_fixed={skip_fixed})")
    logger.info("=" * 70)
    
    progress = load_progress()
    # 如果 skip_fixed=False（全量模式），不使用历史进度
    if skip_fixed:
        done_uids = set(progress.get("phase1_done", []))
    else:
        done_uids = set()
    alias_fixes = progress.get("alias_fixes", {})
    
    persons = get_persons_to_clean(min_aliases=min_aliases, skip_fixed=skip_fixed)
    remaining = [p for p in persons if p["uid"] not in done_uids]
    
    logger.info(f"需要清洗的人物总数: {len(persons)}")
    logger.info(f"已完成: {len(done_uids)}, 待处理: {len(remaining)}")
    
    fixed_count = 0
    skipped_count = 0
    error_count = 0
    
    for i, person in enumerate(remaining):
        uid = person["uid"]
        name = person["name"]
        aliases = person["aliases"]
        
        logger.info(f"\n[{i+1}/{len(remaining)}] {name} ({uid}) - {len(aliases)}个别名")
        logger.info(f"  当前别名: {aliases}")
        
        # 调用 LLM
        result = clean_aliases_with_llm(person)
        
        if result is None:
            logger.warning(f"  ✗ LLM 处理失败，跳过")
            error_count += 1
            # 仍然标记为已处理，避免无限重试
            done_uids.add(uid)
            progress["phase1_done"] = list(done_uids)
            continue
        
        correct = result.get("correct_aliases", [])
        wrong = result.get("wrong_aliases", [])
        
        if wrong:
            logger.info(f"  ✓ 正确别名 ({len(correct)}): {correct}")
            logger.info(f"  ✗ 错误别名 ({len(wrong)}): {wrong}")
            
            # 写入 Neo4j
            apply_alias_fix(uid, correct)
            fixed_count += 1
            
            # 记录修复详情
            alias_fixes[uid] = {
                "name": name,
                "original_aliases": aliases,
                "correct_aliases": correct,
                "removed_aliases": wrong,
            }
        else:
            logger.info(f"  ○ 别名全部正确，无需修改")
            skipped_count += 1
        
        # 更新进度
        done_uids.add(uid)
        progress["phase1_done"] = list(done_uids)
        progress["alias_fixes"] = alias_fixes
        
        # 定期保存进度
        if (i + 1) % BATCH_SAVE_INTERVAL == 0:
            save_progress(progress)
            logger.info(f"  --- 进度已保存 ({len(done_uids)}/{len(persons)}) ---")
        
        # LLM 调用间隔
        time.sleep(LLM_CALL_INTERVAL)
    
    # 最终保存
    save_progress(progress)
    
    logger.info("\n" + "=" * 70)
    logger.info(f"阶段一完成:")
    logger.info(f"  修复: {fixed_count} 个人物")
    logger.info(f"  无需修改: {skipped_count} 个人物")
    logger.info(f"  LLM 失败: {error_count} 个人物")
    logger.info(f"  修复详情已保存至: {PROGRESS_FILE}")
    logger.info("=" * 70)
    
    return fixed_count


# ━━━━━━━━ 阶段二：事件关系修复 ━━━━━━━━

def get_all_person_name_map() -> dict[str, str]:
    """构建 名字/别名 -> uid 的映射表（清洗后的）"""
    results = neo4j_conn.run_query("""
    MATCH (p:Person)
    RETURN p.uid AS uid, p.original_name AS name, p.aliases AS aliases
    """)
    
    name_map = {}
    for r in results:
        uid = r["uid"]
        name_map[r["name"]] = uid
        for alias in (r["aliases"] or []):
            if alias and len(alias) > 1:  # 跳过单字名
                # 如果同一个名字映射到多个uid，保留original_name匹配的
                if alias not in name_map:
                    name_map[alias] = uid
    
    return name_map


def run_phase2_event_relations():
    """阶段二：修复事件关系"""
    logger.info("=" * 70)
    logger.info("阶段二：事件关系修复")
    logger.info("=" * 70)
    
    # 1. 获取所有人物的死亡年份
    death_years = {}
    results = neo4j_conn.run_query("""
    MATCH (p:Person)
    WHERE p.death_year IS NOT NULL
    RETURN p.uid AS uid, p.death_year AS dy
    """)
    for r in results:
        death_years[r["uid"]] = r["dy"]
    
    logger.info(f"有死亡年份的人物: {len(death_years)}")
    
    # 2. 移除死后错误关系
    logger.info("\n--- 步骤 1: 移除死后错误关系 ---")
    total_removed = 0
    
    for uid, dy in death_years.items():
        # 查找该人物的死后事件关系
        post_death = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[:PARTICIPATED_IN]->(e:Event)
        WHERE e.year > $death_year
        RETURN e.uid AS euid, e.name AS ename, e.year AS year, 
               e.description AS desc
        """, {"uid": uid, "death_year": dy})
        
        if not post_death:
            continue
        
        # 过滤掉追思类事件
        to_remove = []
        for e in post_death:
            desc = (e.get("desc") or "") + (e.get("ename") or "")
            if not any(kw in desc for kw in MEMORIAL_KEYWORDS):
                to_remove.append(e["euid"])
        
        if to_remove:
            neo4j_conn.run_write("""
            MATCH (p:Person {uid: $uid})-[r:PARTICIPATED_IN]->(e:Event)
            WHERE e.uid IN $event_uids
            DELETE r
            """, {"uid": uid, "event_uids": to_remove})
            total_removed += len(to_remove)
    
    logger.info(f"移除死后错误关系: {total_removed} 条")
    
    # 3. 构建名字->uid映射
    logger.info("\n--- 步骤 2: 补建缺失的事件关系 ---")
    name_map = get_all_person_name_map()
    logger.info(f"名字映射表大小: {len(name_map)}")
    
    # 4. 遍历所有事件，基于 participants 和 description 补建关系
    events = neo4j_conn.run_query("""
    MATCH (e:Event)
    RETURN e.uid AS uid, e.name AS name, e.year AS year,
           e.participants AS participants, e.description AS desc
    """)
    logger.info(f"总事件数: {len(events)}")
    
    total_added = 0
    batch = []
    
    for e in events:
        euid = e["uid"]
        year = e.get("year")
        participants = e.get("participants") or []
        desc = (e.get("desc") or "") + " " + (e.get("name") or "")
        
        # 收集应该关联的人物uid
        should_link = set()
        
        # 从 participants 字段
        for pname in participants:
            uid = name_map.get(pname)
            if uid:
                should_link.add(uid)
        
        # 从 description 中查找人名（只查 original_name 的精确匹配）
        for name, uid in name_map.items():
            if len(name) >= 2 and name in desc:
                should_link.add(uid)
        
        # 过滤：死后事件不关联
        final_link = set()
        for uid in should_link:
            dy = death_years.get(uid)
            if dy and year and year > dy:
                # 检查是否追思类
                if any(kw in desc for kw in MEMORIAL_KEYWORDS):
                    final_link.add(uid)
                # 否则跳过
            else:
                final_link.add(uid)
        
        if final_link:
            batch.append((euid, final_link))
        
        # 批量写入
        if len(batch) >= 200:
            added = _flush_event_links(batch)
            total_added += added
            batch = []
    
    # 处理剩余
    if batch:
        added = _flush_event_links(batch)
        total_added += added
    
    logger.info(f"\n补建事件关系: {total_added} 条")
    
    # 5. 清理重复关系
    logger.info("\n--- 步骤 3: 清理重复关系 ---")
    neo4j_conn.run_write("""
    MATCH (p)-[r]->(p)
    DELETE r
    """)
    neo4j_conn.run_write("""
    MATCH (a)-[r]->(b)
    WITH a, b, type(r) AS relType, collect(r) AS rels
    WHERE size(rels) > 1
    FOREACH (r IN tail(rels) | DELETE r)
    """)
    logger.info("重复关系已清理")
    
    # 更新进度
    progress = load_progress()
    progress["phase2_done"] = True
    save_progress(progress)
    
    logger.info("\n" + "=" * 70)
    logger.info(f"阶段二完成:")
    logger.info(f"  移除死后错误关系: {total_removed} 条")
    logger.info(f"  补建缺失关系: {total_added} 条")
    logger.info("=" * 70)


def _flush_event_links(batch: list[tuple[str, set[str]]]) -> int:
    """批量写入事件关系，返回新建的关系数"""
    added = 0
    for euid, person_uids in batch:
        for puid in person_uids:
            # 使用 MERGE 避免重复
            result = neo4j_conn.run_query("""
            MATCH (p:Person {uid: $puid})-[r:PARTICIPATED_IN]->(e:Event {uid: $euid})
            RETURN count(r) AS cnt
            """, {"puid": puid, "euid": euid})
            
            if result and result[0]["cnt"] == 0:
                neo4j_conn.run_write("""
                MATCH (p:Person {uid: $puid})
                MATCH (e:Event {uid: $euid})
                MERGE (p)-[r:PARTICIPATED_IN]->(e)
                SET r.role = '参与者'
                """, {"puid": puid, "euid": euid})
                added += 1
    return added


# ━━━━━━━━ 验证 ━━━━━━━━

def verify_results():
    """验证清洗效果"""
    logger.info("=" * 70)
    logger.info("验证清洗效果")
    logger.info("=" * 70)
    
    # 1. 检查还有没有别名明显异常的人物
    logger.info("\n--- 别名数 >= 8 的人物 ---")
    results = neo4j_conn.run_query("""
    MATCH (p:Person)
    WHERE size(p.aliases) >= 8
    RETURN p.uid AS uid, p.original_name AS name, 
           size(p.aliases) AS alias_count, p.aliases AS aliases
    ORDER BY alias_count DESC
    LIMIT 20
    """)
    for r in results:
        print(f"  {r['name']:15s} aliases={r['alias_count']:3d} | {r['aliases']}")
    
    # 2. 检查死后事件关系
    logger.info("\n--- 死后事件关系检查 ---")
    results = neo4j_conn.run_query("""
    MATCH (p:Person)-[:PARTICIPATED_IN]->(e:Event)
    WHERE p.death_year IS NOT NULL AND e.year IS NOT NULL 
          AND e.year > p.death_year
    WITH p, count(e) AS post_death
    WHERE post_death > 3
    RETURN p.original_name AS name, p.death_year AS dy, post_death
    ORDER BY post_death DESC
    LIMIT 15
    """)
    for r in results:
        print(f"  {r['name']:15s} 卒年={r['dy']} 死后事件={r['post_death']}")
    
    # 3. 核心人物事件数
    logger.info("\n--- 核心人物事件数 ---")
    core_uids = [
        ("person_li_cunxu", "李存勖"),
        ("person_li_keyong", "李克用"),
        ("person_zhuwen", "朱温"),
        ("person_li_siyuan", "李嗣源"),
        ("person_shi_jingtang", "石敬瑭"),
        ("person_liu_zhiyuan", "刘知远"),
        ("person_guo_wei", "郭威"),
        ("person_chai_rong", "柴荣"),
        ("person_zhao_kuangyin", "赵匡胤"),
    ]
    for uid, expected_name in core_uids:
        cnt = neo4j_conn.run_query("""
        MATCH (p:Person {uid: $uid})-[:PARTICIPATED_IN]->(e:Event)
        RETURN count(e) AS cnt, p.original_name AS name
        """, {"uid": uid})
        if cnt:
            print(f"  {cnt[0]['name']:15s} 事件数: {cnt[0]['cnt']}")


# ━━━━━━━━ 主入口 ━━━━━━━━

def main():
    """主程序"""
    import argparse
    
    parser = argparse.ArgumentParser(description="LLM 批量清洗脚本")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], default=0,
                        help="运行阶段: 1=别名清洗, 2=事件关系修复, 3=验证, 0=全部")
    parser.add_argument("--verify-only", action="store_true",
                        help="仅验证结果")
    parser.add_argument("--full", action="store_true",
                        help="全量模式：不跳过任何人物，忽略历史进度")
    parser.add_argument("--min-aliases", type=int, default=2,
                        help="最少别名数（默认2）")
    args = parser.parse_args()
    
    start_time = datetime.now()
    logger.info(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if args.verify_only:
        verify_results()
        return
    
    skip_fixed = not args.full
    
    if args.phase == 0 or args.phase == 1:
        run_phase1_alias_cleanup(min_aliases=args.min_aliases, skip_fixed=skip_fixed)
    
    if args.phase == 0 or args.phase == 2:
        run_phase2_event_relations()
    
    if args.phase == 0 or args.phase == 3:
        verify_results()
    
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    logger.info(f"\n总耗时: {elapsed:.1f} 秒 ({elapsed/60:.1f} 分钟)")


if __name__ == "__main__":
    main()

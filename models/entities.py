"""五代知识图谱实体模型定义

Schema 设计原则：
- 人物 (Person)：必须包含 original_name, aliases, loyalty
- 事件 (Event)：区分 War, Coup, Succession 等
- 政权 (Dynasty)：五代 + 关联的十国/契丹
- 地点 (Place)：战争发生地、都城等

改名/赐名处理：
- 五代时期人物改名极为频繁（赐姓、赐名、即位改名、避讳改名等）
- Person.aliases 必须包含该人物在历史上的所有已知名字变体
- 入库时通过 name + aliases 做模糊匹配，避免重复创建节点
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ─────────────────────────── 实体模型 ───────────────────────────


class Person(BaseModel):
    """人物实体

    关于改名：五代时期改名极为普遍，例如：
    - 元行钦 → 被赐名李绍荣
    - 朱温（本名朱温 → 赐名朱全忠 → 即位改朱晃）
    - 李嗣源（本名邈佶烈 → 被赐名李嗣源）

    aliases 必须收录一个人物的所有已知名字。
    """
    uid: str = Field(description="唯一标识符，格式 person_最常用拼音名")
    original_name: str = Field(description="最常用的名字（不一定是本名，而是史书中最常使用的称呼）")
    aliases: list[str] = Field(
        default_factory=list,
        description="所有别名/赐名/曾用名/本名，例如 ['朱全忠', '朱晃', '朱三']",
    )
    role: str = Field(default="其他", description="角色：皇帝/将领/大臣/宦官/叛将/藩镇节度使/后妃/文人/僧侣/其他")
    loyalty: list[str] = Field(default_factory=list, description="归属势力列表（按时间顺序）")
    birth_year: Optional[int] = Field(default=None, description="出生年份")
    death_year: Optional[int] = Field(default=None, description="死亡年份")
    death_cause: Optional[str] = Field(default=None, description="死因")
    description: str = Field(default="", description="简要描述")

    def all_names(self) -> set[str]:
        """返回该人物的所有名字（用于合并匹配）"""
        names = {self.original_name}
        names.update(self.aliases)
        # 去掉空字符串
        names.discard("")
        return names

    def neo4j_properties(self) -> dict:
        """转换为 Neo4j 节点属性"""
        props = {
            "uid": self.uid,
            "original_name": self.original_name,
            "aliases": self.aliases,
            "role": self.role,
            "loyalty": self.loyalty,
            "description": self.description,
        }
        if self.birth_year is not None:
            props["birth_year"] = self.birth_year
        if self.death_year is not None:
            props["death_year"] = self.death_year
        if self.death_cause is not None:
            props["death_cause"] = self.death_cause
        return props


class Dynasty(BaseModel):
    """政权实体"""
    uid: str = Field(description="唯一标识符，如 dynasty_later_liang")
    name: str = Field(description="政权名称")
    founder: Optional[str] = Field(default=None, description="建国者名字")
    capital: Optional[str] = Field(default=None, description="都城")
    start_year: Optional[int] = Field(default=None, description="建国年份")
    end_year: Optional[int] = Field(default=None, description="灭亡年份")
    description: str = Field(default="", description="简要描述")

    def neo4j_properties(self) -> dict:
        props = {
            "uid": self.uid,
            "name": self.name,
            "description": self.description,
        }
        if self.founder:
            props["founder"] = self.founder
        if self.capital:
            props["capital"] = self.capital
        if self.start_year is not None:
            props["start_year"] = self.start_year
        if self.end_year is not None:
            props["end_year"] = self.end_year
        return props


class Event(BaseModel):
    """事件实体"""
    uid: str = Field(description="唯一标识符")
    name: str = Field(description="事件名称")
    event_type: str = Field(default="其他", description="事件类型：战争/政变/皇位更替/结盟/背叛事件/暗杀/叛乱/其他")
    year: Optional[int] = Field(default=None, description="发生年份")
    location: Optional[str] = Field(default=None, description="发生地点")
    participants: list[str] = Field(default_factory=list, description="参与人物名字列表")
    outcome: Optional[str] = Field(default=None, description="事件结果")
    description: str = Field(default="", description="详细描述")

    def neo4j_properties(self) -> dict:
        props = {
            "uid": self.uid,
            "name": self.name,
            "event_type": self.event_type,
            "participants": self.participants,
            "description": self.description,
        }
        if self.year is not None:
            props["year"] = self.year
        if self.location:
            props["location"] = self.location
        if self.outcome:
            props["outcome"] = self.outcome
        return props


class Place(BaseModel):
    """地点实体"""
    uid: str = Field(description="唯一标识符")
    name: str = Field(description="地名")
    modern_name: Optional[str] = Field(default=None, description="今地名")
    description: str = Field(default="", description="简要描述")

    def neo4j_properties(self) -> dict:
        props = {
            "uid": self.uid,
            "name": self.name,
            "description": self.description,
        }
        if self.modern_name:
            props["modern_name"] = self.modern_name
        return props


# ─────────────────────────── 关系模型 ───────────────────────────


class Relation(BaseModel):
    """关系定义 - 使用字符串而非枚举，以容纳更多关系类型"""
    source: str = Field(description="源节点名字（人物最常用名字）")
    target: str = Field(description="目标节点名字（人物最常用名字）")
    relation_type: str = Field(
        description="关系类型，常见：FATHER_OF / MOTHER_OF / SIBLING / SPOUSE / "
                    "ADOPTED_SON / BETRAYED / KILLED / REPLACED / SERVED / "
                    "COMMANDED / ALLIED_WITH / SUCCEEDED / SUBORDINATE / ADVISOR / "
                    "RIVAL / SURRENDERED_TO / MARRIED_INTO 等"
    )
    year: Optional[int] = Field(default=None, description="关系发生年份")
    description: str = Field(default="", description="关系描述")

    def neo4j_properties(self) -> dict:
        props = {"description": self.description}
        if self.year is not None:
            props["year"] = self.year
        return props


# ─────────────────────────── 提取结果容器 ───────────────────────────


class ExtractionResult(BaseModel):
    """单个文本块的知识抽取结果"""
    persons: list[Person] = Field(default_factory=list)
    dynasties: list[Dynasty] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    source_text: str = Field(default="", description="原文片段")
    source_chapter: str = Field(default="", description="所属章节")

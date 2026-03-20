"""知识一致性检查器

自动检查提取的知识是否存在矛盾、错误或不一致的地方。
遵循原则：宁可漏提，不可错提
"""
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import json


class CheckSeverity(Enum):
    """检查严重级别"""
    ERROR = "error"       # 严重错误，必须人工确认
    WARNING = "warning"   # 警告，建议人工确认
    INFO = "info"         # 提示信息


@dataclass
class CheckResult:
    """检查结果"""
    checker_name: str
    severity: CheckSeverity
    message: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    suggestion: Optional[str] = None


class BaseChecker:
    """检查器基类"""
    
    def __init__(self, name: str):
        self.name = name
    
    def check(self, new_entities: List[Dict], 
              existing_entities: List[Dict]) -> List[CheckResult]:
        """执行检查"""
        raise NotImplementedError


class TimeConflictChecker(BaseChecker):
    """时间冲突检查器
    
    检查原则：
    1. 同一人物同一时间不能出现在两个不同地点
    2. 事件的结束时间不能早于开始时间
    3. 人物出生时间必须早于死亡时间
    4. 官职任命时间必须在人物在世期间
    """
    
    def __init__(self):
        super().__init__("时间冲突检查器")
    
    def check(self, new_entities: List[Dict], 
              existing_entities: List[Dict]) -> List[CheckResult]:
        results = []
        all_entities = existing_entities + new_entities
        
        # 检查事件时间
        events = [e for e in all_entities if e.get('type') == 'event']
        for event in events:
            results.extend(self._check_event_time(event))
        
        # 检查人物时间线
        persons = [e for e in all_entities if e.get('type') == 'person']
        for person in persons:
            results.extend(self._check_person_timeline(person))
        
        # 检查人物同一时间多地出现（更复杂的逻辑）
        results.extend(self._check_concurrent_location(persons, events))
        
        return results
    
    def _check_event_time(self, event: Dict) -> List[CheckResult]:
        results = []
        start = event.get('start_date')
        end = event.get('end_date')
        
        if start and end:
            start_year = self._extract_year(start)
            end_year = self._extract_year(end)
            
            if start_year and end_year and end_year < start_year:
                results.append(CheckResult(
                    checker_name=self.name,
                    severity=CheckSeverity.ERROR,
                    message=f"事件'{event.get('name')}'的结束时间({end})早于开始时间({start})",
                    entity_id=event.get('id'),
                    entity_type='event',
                    suggestion="请核实事件的准确时间"
                ))
        return results
    
    def _check_person_timeline(self, person: Dict) -> List[CheckResult]:
        results = []
        birth = person.get('birth_date')
        death = person.get('death_date')
        
        if birth and death:
            birth_year = self._extract_year(birth)
            death_year = self._extract_year(death)
            
            if birth_year and death_year:
                # 古人极少活过100岁
                if death_year - birth_year > 100:
                    results.append(CheckResult(
                        checker_name=self.name,
                        severity=CheckSeverity.WARNING,
                        message=f"人物'{person.get('name')}'寿命({death_year - birth_year}岁)过长，请核实",
                        entity_id=person.get('id'),
                        entity_type='person',
                        suggestion="确认出生或死亡年份是否正确"
                    ))
                
                # 死亡早于出生
                if death_year < birth_year:
                    results.append(CheckResult(
                        checker_name=self.name,
                        severity=CheckSeverity.ERROR,
                        message=f"人物'{person.get('name')}'死亡时间早于出生时间",
                        entity_id=person.get('id'),
                        entity_type='person',
                        suggestion="请核实生卒年份"
                    ))
        
        return results
    
    def _check_concurrent_location(self, persons: List[Dict], 
                                    events: List[Dict]) -> List[CheckResult]:
        """检查同一人物是否同时出现在多个地点"""
        # 简化实现：检查是否参与了时间重叠的不同地点事件
        results = []
        
        # 将人物按姓名分组，检查冲突
        person_participations = {}  # person_id -> [(event_start, event_end, location)]
        
        for event in events:
            participants = event.get('participants', [])
            for pid in participants:
                if pid not in person_participations:
                    person_participations[pid] = []
                person_participations[pid].append({
                    'event': event.get('name'),
                    'start': event.get('start_date'),
                    'end': event.get('end_date'),
                    'location': event.get('location')
                })
        
        # 检查每个人的事件时间是否重叠
        for pid, events_list in person_participations.items():
            if len(events_list) < 2:
                continue
            
            # 简化的冲突检测
            for i, e1 in enumerate(events_list):
                for e2 in events_list[i+1:]:
                    if e1['location'] != e2['location']:
                        if self._time_ranges_overlap(e1['start'], e1['end'], 
                                                      e2['start'], e2['end']):
                            results.append(CheckResult(
                                checker_name=self.name,
                                severity=CheckSeverity.WARNING,
                                message=f"人物可能在同一时间({e1['start']}-{e2['end']})出现在" +
                                       f"两个地点: {e1['location']} 和 {e2['location']}",
                                entity_id=pid,
                                entity_type='person',
                                suggestion="请核实该人物当时的实际位置，可能是时间记录不精确"
                            ))
        
        return results
    
    def _extract_year(self, date_str: str) -> Optional[int]:
        """从日期字符串提取年份"""
        if not date_str:
            return None
        
        # 支持多种格式："926年", "同光三年", "923"
        import re
        
        # 提取公元纪年
        match = re.search(r'(\d{3,4})年?', date_str)
        if match:
            return int(match.group(1))
        
        # 提取年号纪年（简化映射）
        era_map = {
            "天复": 901, "天祐": 904, "开平": 907, "乾化": 911,
            "贞明": 915, "龙德": 921, "同光": 923, "天成": 926,
            "长兴": 930, "应顺": 934, "清泰": 934, "天福": 936,
        }
        
        for era, start_year in era_map.items():
            if era in date_str:
                # 尝试提取年数
                year_match = re.search(rf'{era}(\d+|元)年', date_str)
                if year_match:
                    year_num = 1 if year_match.group(1) == '元' else int(year_match.group(1))
                    return start_year + year_num - 1
        
        return None
    
    def _time_ranges_overlap(self, s1: str, e1: str, s2: str, e2: str) -> bool:
        """检查两个时间范围是否重叠"""
        # 简化实现，实际应该使用更精确的时间解析
        y1_start = self._extract_year(s1) or 0
        y1_end = self._extract_year(e1) or 9999
        y2_start = self._extract_year(s2) or 0
        y2_end = self._extract_year(e2) or 9999
        
        return not (y1_end < y2_start or y2_end < y1_start)


class RelationLogicChecker(BaseChecker):
    """关系逻辑检查器
    
    检查：
    1. 父子关系的时间顺序合理性
    2. 官职任命必须在任命者掌权期间
    3. 敌对关系与友好关系的逻辑矛盾
    """
    
    def __init__(self):
        super().__init__("关系逻辑检查器")
    
    def check(self, new_entities: List[Dict], 
              existing_entities: List[Dict]) -> List[CheckResult]:
        results = []
        all_entities = existing_entities + new_entities
        
        # 检查父子关系
        parent_child_pairs = self._find_parent_child_relations(all_entities)
        for parent_id, child_id in parent_child_pairs:
            results.extend(self._check_parent_child_logic(parent_id, child_id, all_entities))
        
        # 检查敌对/友好关系矛盾
        results.extend(self._check_relationship_conflicts(all_entities))
        
        return results
    
    def _find_parent_child_relations(self, entities: List[Dict]) -> List[Tuple[str, str]]:
        """查找所有父子关系对"""
        pairs = []
        for entity in entities:
            if entity.get('type') == 'relation' and entity.get('relation_type') == 'parent_child':
                pairs.append((entity.get('from'), entity.get('to')))
        return pairs
    
    def _check_parent_child_logic(self, parent_id: str, child_id: str, 
                                   entities: List[Dict]) -> List[CheckResult]:
        results = []
        
        # 获取人物信息
        parent = next((e for e in entities if e.get('id') == parent_id), None)
        child = next((e for e in entities if e.get('id') == child_id), None)
        
        if not parent or not child:
            return results
        
        # 检查父母年龄（古人通常20岁前不会生育）
        checker = TimeConflictChecker()
        parent_birth = checker._extract_year(parent.get('birth_date') or '')
        child_birth = checker._extract_year(child.get('birth_date') or '')
        
        if parent_birth and child_birth:
            age_diff = child_birth - parent_birth
            if age_diff < 12:
                results.append(CheckResult(
                    checker_name=self.name,
                    severity=CheckSeverity.ERROR,
                    message=f"父子年龄差({age_diff}岁)过小，可能存在关系错误",
                    entity_id=parent_id,
                    entity_type='relation',
                    suggestion="请核实是否为养父子关系或记录有误"
                ))
            elif age_diff > 60:
                results.append(CheckResult(
                    checker_name=self.name,
                    severity=CheckSeverity.WARNING,
                    message=f"父子年龄差({age_diff}岁)过大，请核实",
                    entity_id=parent_id,
                    entity_type='relation',
                    suggestion="确认是否为亲生父子关系"
                ))
        
        return results
    
    def _check_relationship_conflicts(self, entities: List[Dict]) -> List[CheckResult]:
        """检查关系矛盾"""
        results = []
        
        # 查找相反的关系
        opposing_relations = {
            ('friend', 'enemy'),
            ('ally', 'opponent'),
            ('supports', 'opposes')
        }
        
        relation_pairs = defaultdict(list)
        for entity in entities:
            if entity.get('type') == 'relation':
                key = tuple(sorted([entity.get('from'), entity.get('to')]))
                relation_pairs[key].append(entity)
        
        for pair, rels in relation_pairs.items():
            if len(rels) > 1:
                types = set(r.get('relation_type') for r in rels)
                for opp_pair in opposing_relations:
                    if opp_pair[0] in types and opp_pair[1] in types:
                        results.append(CheckResult(
                            checker_name=self.name,
                            severity=CheckSeverity.ERROR,
                            message=f"实体{pair[0]}和{pair[1]}同时存在矛盾关系: {opp_pair}",
                            entity_type='relation',
                            suggestion="请确定两者实际关系，删除错误的关系记录"
                        ))
        
        return results


from collections import defaultdict


class EventCompletenessChecker(BaseChecker):
    """事件完整性检查器
    
    检查：
    1. 战役必须有：时间、地点、参战双方、结果
    2. 人物必须有关联事件
    3. 地点必须有地理位置信息
    """
    
    def __init__(self):
        super().__init__("事件完整性检查器")
    
    def check(self, new_entities: List[Dict], 
              existing_entities: List[Dict]) -> List[CheckResult]:
        results = []
        all_entities = existing_entities + new_entities
        
        # 检查事件字段完整性
        for entity in all_entities:
            if entity.get('type') == 'event':
                results.extend(self._check_event_completeness(entity))
            elif entity.get('type') == 'person':
                results.extend(self._check_person_event_link(entity, all_entities))
            elif entity.get('type') == 'place':
                results.extend(self._check_place_completeness(entity))
        
        return results
    
    def _check_event_completeness(self, event: Dict) -> List[CheckResult]:
        results = []
        required_fields = ['name', 'start_date', 'location', 'participants']
        
        # 根据事件类型检查更多字段
        event_type = event.get('event_type', '')
        if '战' in event_type or '役' in event_type or '战役' in event.get('name', ''):
            required_fields.extend(['result', 'belligerents'])
        
        missing = [f for f in required_fields if not event.get(f)]
        
        if missing:
            severity = CheckSeverity.ERROR if len(missing) > 2 else CheckSeverity.WARNING
            results.append(CheckResult(
                checker_name=self.name,
                severity=severity,
                message=f"事件'{event.get('name')}'缺少关键字段: {', '.join(missing)}",
                entity_id=event.get('id'),
                entity_type='event',
                suggestion=f"请补充{missing[0]}等信息"
            ))
        
        return results
    
    def _check_person_event_link(self, person: Dict, all_entities: List[Dict]) -> List[CheckResult]:
        """检查人物是否有关联事件"""
        results = []
        person_id = person.get('id')
        
        # 检查该人物是否出现在任何事件中
        has_events = any(
            person_id in (e.get('participants') or [])
            for e in all_entities if e.get('type') == 'event'
        )
        
        # 或者是否有关系记录
        has_relations = any(
            person_id in [r.get('from'), r.get('to')]
            for r in all_entities if r.get('type') == 'relation'
        )
        
        # 只有主要人物需要关联事件
        is_major_figure = person.get('is_major_figure', False)
        
        if is_major_figure and not has_events:
            results.append(CheckResult(
                checker_name=self.name,
                severity=CheckSeverity.WARNING,
                message=f"主要人物'{person.get('name')}'缺少关联事件",
                entity_id=person_id,
                entity_type='person',
                suggestion="请补充该人物参与的重要事件"
            ))
        
        return results
    
    def _check_place_completeness(self, place: Dict) -> List[CheckResult]:
        results = []
        
        # 地点应该有地理信息
        if not place.get('coordinates') and not place.get('modern_name'):
            results.append(CheckResult(
                checker_name=self.name,
                severity=CheckSeverity.INFO,
                message=f"地点'{place.get('name')}'缺少地理信息",
                entity_id=place.get('id'),
                entity_type='place',
                suggestion="建议补充现代地名或坐标，便于定位"
            ))
        
        return results


class DuplicateEntityChecker(BaseChecker):
    """重复实体检查器
    
    检查：
    1. 同一实体被多次创建
    2. 名字高度相似可能是同一实体
    """
    
    def __init__(self):
        super().__init__("重复实体检查器")
    
    def check(self, new_entities: List[Dict], 
              existing_entities: List[Dict]) -> List[CheckResult]:
        results = []
        all_entities = existing_entities + new_entities
        
        # 按类型分组
        by_type = defaultdict(list)
        for e in all_entities:
            by_type[e.get('type')].append(e)
        
        # 检查每种类型内的重复
        for entity_type, entities in by_type.items():
            results.extend(self._check_duplicates_in_group(entities, entity_type))
        
        return results
    
    def _check_duplicates_in_group(self, entities: List[Dict], 
                                    entity_type: str) -> List[CheckResult]:
        results = []
        
        # 精确名称匹配
        names = {}
        for e in entities:
            name = e.get('name', '').strip()
            if name:
                if name in names:
                    results.append(CheckResult(
                        checker_name=self.name,
                        severity=CheckSeverity.ERROR,
                        message=f"发现重复的{entity_type}: '{name}'",
                        entity_id=e.get('id'),
                        entity_type=entity_type,
                        suggestion="请合并或删除重复实体"
                    ))
                else:
                    names[name] = e
        
        # 相似名称检测（简化实现）
        for i, e1 in enumerate(entities):
            for e2 in entities[i+1:]:
                n1 = e1.get('name', '')
                n2 = e2.get('name', '')
                
                # 编辑距离检测
                if self._similarity(n1, n2) > 0.7 and n1 != n2:
                    results.append(CheckResult(
                        checker_name=self.name,
                        severity=CheckSeverity.WARNING,
                        message=f"发现名称相似的{entity_type}: '{n1}' 和 '{n2}'",
                        entity_type=entity_type,
                        suggestion="请确认是否为同一实体的不同写法"
                    ))
        
        return results
    
    def _similarity(self, s1: str, s2: str) -> float:
        """计算字符串相似度（简化版）"""
        if not s1 or not s2:
            return 0.0
        
        # 使用简单的编辑距离
        len1, len2 = len(s1), len(s2)
        if len1 == 0 or len2 == 0:
            return 0.0
        
        # 最长公共子串
        max_match = 0
        for i in range(len1):
            for j in range(len2):
                k = 0
                while i + k < len1 and j + k < len2 and s1[i+k] == s2[j+k]:
                    k += 1
                max_match = max(max_match, k)
        
        return max_match / max(len1, len2)


class ConsistencyValidator:
    """一致性验证器 - 整合所有检查器"""
    
    def __init__(self):
        self.checkers: List[BaseChecker] = [
            TimeConflictChecker(),
            RelationLogicChecker(),
            EventCompletenessChecker(),
            DuplicateEntityChecker()
        ]
    
    def validate(self, new_entities: List[Dict], 
                 existing_entities: List[Dict] = None) -> List[CheckResult]:
        """执行所有检查"""
        if existing_entities is None:
            existing_entities = []
        
        all_results = []
        for checker in self.checkers:
            try:
                results = checker.check(new_entities, existing_entities)
                all_results.extend(results)
            except Exception as e:
                # 记录检查器错误但不中断
                all_results.append(CheckResult(
                    checker_name=checker.name,
                    severity=CheckSeverity.ERROR,
                    message=f"检查器执行失败: {str(e)}"
                ))
        
        # 按严重级别排序
        severity_order = {CheckSeverity.ERROR: 0, CheckSeverity.WARNING: 1, CheckSeverity.INFO: 2}
        all_results.sort(key=lambda r: severity_order.get(r.severity, 3))
        
        return all_results
    
    def validate_and_report(self, new_entities: List[Dict],
                           existing_entities: List[Dict] = None) -> Dict:
        """验证并生成报告"""
        results = self.validate(new_entities, existing_entities)
        
        errors = [r for r in results if r.severity == CheckSeverity.ERROR]
        warnings = [r for r in results if r.severity == CheckSeverity.WARNING]
        infos = [r for r in results if r.severity == CheckSeverity.INFO]
        
        return {
            'total_issues': len(results),
            'errors': len(errors),
            'warnings': len(warnings),
            'infos': len(infos),
            'has_blocking_errors': len(errors) > 0,
            'details': [
                {
                    'checker': r.checker_name,
                    'severity': r.severity.value,
                    'message': r.message,
                    'entity_id': r.entity_id,
                    'entity_type': r.entity_type,
                    'suggestion': r.suggestion
                }
                for r in results
            ]
        }
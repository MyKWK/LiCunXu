"""别名解析器 - 处理五代历史人物的复杂别名系统

五代时期常见别名类型：
1. 赐姓名：如李存勖赐名朱邪克用族人为李嗣源等
2. 避讳改名
3. 官职代称：如"刘守光"又称"燕王"
4. 地名称呼：如"晋王"=李克用
5. 同音异字：如"存勖"vs"存勗"
"""
from typing import Dict, List, Set, Optional
from dataclasses import dataclass
import json
import re


@dataclass
class PersonAlias:
    """人物别名记录"""
    canonical_name: str  # 标准名
    aliases: Set[str]    # 所有别名
    alias_types: Dict[str, str]  # 别名->类型映射
    source_book: Optional[str] = None  # 来源


class AliasResolver:
    """别名解析器"""
    
    def __init__(self, alias_db_path: Optional[str] = None):
        self.persons: Dict[str, PersonAlias] = {}  # 标准名 -> 记录
        self.alias_to_canonical: Dict[str, str] = {}  # 别名 -> 标准名
        self._canonical_cache: Dict[str, str] = {}  # 缓存
    
    def register_person(self, canonical_name: str, aliases: List[str], 
                       alias_types: Optional[Dict[str, str]] = None):
        """注册一个标准人物及其别名"""
        alias_set = set(aliases)
        alias_set.add(canonical_name)
        
        person = PersonAlias(
            canonical_name=canonical_name,
            aliases=alias_set,
            alias_types=alias_types or {}
        )
        
        self.persons[canonical_name] = person
        
        # 建立所有别名到标准名的映射
        for alias in alias_set:
            normalized = self._normalize(alias)
            self.alias_to_canonical[normalized] = canonical_name
    
    def resolve(self, name: str) -> Optional[str]:
        """将任意名称解析为标准名"""
        # 先查缓存
        if name in self._canonical_cache:
            return self._canonical_cache[name]
        
        normalized = self._normalize(name)
        
        # 直接匹配
        if normalized in self.alias_to_canonical:
            result = self.alias_to_canonical[normalized]
            self._canonical_cache[name] = result
            return result
        
        # 模糊匹配（去掉头衔）
        clean_name = self._remove_titles(normalized)
        if clean_name in self.alias_to_canonical:
            result = self.alias_to_canonical[clean_name]
            self._canonical_cache[name] = result
            return result
        
        # 同音字替换匹配
        phonetic_match = self._phonetic_match(normalized)
        if phonetic_match:
            self._canonical_cache[name] = phonetic_match
            return phonetic_match
        
        return None
    
    def _normalize(self, name: str) -> str:
        """标准化名称（繁体转简体、去空格等）"""
        # 基础清理
        name = name.strip()
        name = re.sub(r'[\s\u3000]+', '', name)
        return name
    
    def _remove_titles(self, name: str) -> str:
        """去掉常见头衔，获取核心人名"""
        titles = ['皇帝', '皇上', '陛下', '殿下', '将军', '节度使', 
                 '刺史', '太守', '刺史', '大王', '王', '公']
        
        for title in titles:
            name = re.sub(f'[\\u4e00-\\u9fa5]*{title}$', '', name)
        return name.strip()
    
    def _phonetic_match(self, name: str) -> Optional[str]:
        """同音字/近音字匹配"""
        # 五代常见同音字映射
        phonetic_map = {
            '勖': '勗', '勗': '勖',
            '嗣': '似', '似': '嗣',
            '汴': '卞', '卞': '汴',
        }
        
        variants = [name]
        for char, variant in phonetic_map.items():
            if char in name:
                variants.append(name.replace(char, variant))
        
        for variant in variants:
            if variant in self.alias_to_canonical:
                return self.alias_to_canonical[variant]
        
        return None
    
    def extract_persons_from_text(self, text: str) -> List[str]:
        """从文本中提取所有识别到的人物标准名"""
        found_persons = set()
        
        # 遍历所有已知的别名进行匹配
        for alias, canonical in self.alias_to_canonical.items():
            # 使用词边界匹配（中文语境下调整）
            pattern = re.escape(alias)
            if re.search(pattern, text):
                found_persons.add(canonical)
        
        return list(found_persons)
    
    def load_from_json(self, filepath: str):
        """从JSON文件加载别名库"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for item in data:
            self.register_person(
                canonical_name=item['canonical_name'],
                aliases=item.get('aliases', []),
                alias_types=item.get('alias_types', {})
            )
    
    def save_to_json(self, filepath: str):
        """保存别名库到JSON"""
        data = []
        for person in self.persons.values():
            data.append({
                'canonical_name': person.canonical_name,
                'aliases': list(person.aliases),
                'alias_types': person.alias_types
            })
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# 五代十国核心人物初始别名库
FIVE_DYNASTIES_CORE_PERSONS = [
    {
        "canonical_name": "李存勖",
        "aliases": ["后唐庄宗", "庄宗", "李存勗", "唐庄宗", "李天下", "嗣源兄", "李亚子", "亚子"],
        "alias_types": {
            "李存勗": "同音异字",
            "后唐庄宗": "帝号",
            "庄宗": "简称",
            "唐庄宗": "简称",
            "李天下": "艺名/戏称",
            "李亚子": "昵称",
            "亚子": "昵称"
        }
    },
    {
        "canonical_name": "李克用",
        "aliases": ["晋王", "后唐太祖", "沙陀晋王", "唐太祖", "李鸦儿", "独眼龙", "李国昌", "朱邪赤心"],
        "alias_types": {
            "晋王": "爵位",
            "后唐太祖": "追谥帝号",
            "唐太祖": "追谥帝号简称",
            "沙陀晋王": "族群+爵位",
            "李鸦儿": "小名",
            "独眼龙": "绰号",
            "李国昌": "赐名",
            "朱邪赤心": "赐名"
        }
    },
    {
        "canonical_name": "李嗣源",
        "aliases": ["后唐明宗", "明宗", "唐明宗", "李霓", "李嗣源", "晋王养子"],
        "alias_types": {
            "后唐明宗": "帝号",
            "明宗": "简称",
            "唐明宗": "简称",
            "李霓": "早年名",
            "晋王养子": "身份称谓"
        }
    },
    {
        "canonical_name": "朱温",
        "aliases": ["后梁太祖", "梁太祖", "朱全忠", "朱晃", "黄巢降将", "宣武节度使", "梁王"],
        "alias_types": {
            "后梁太祖": "帝号",
            "梁太祖": "简称",
            "朱全忠": "赐名",
            "朱晃": "称帝后改名",
            "梁王": "爵位"
        }
    },
    {
        "canonical_name": "石敬瑭",
        "aliases": ["后晋高祖", "晋高祖", "儿皇帝", "后唐河东节度使"],
        "alias_types": {
            "后晋高祖": "帝号",
            "儿皇帝": "历史性称呼",
            "后唐河东节度使": "官职"
        }
    }
]


def create_five_dynasties_resolver() -> AliasResolver:
    """创建包含五代核心人物别名的解析器"""
    resolver = AliasResolver()
    
    for person in FIVE_DYNASTIES_CORE_PERSONS:
        resolver.register_person(
            canonical_name=person['canonical_name'],
            aliases=person['aliases'],
            alias_types=person.get('alias_types', {})
        )
    
    return resolver
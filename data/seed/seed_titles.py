"""五代藩镇官职种子数据 — 权威基准

此文件中的官职数据来自史学家对唐代藩镇官制的系统总结（对应 fanzhen.html 中的静态数据），
拥有 **最高优先级**：
1. source="seed" 标记为种子数据
2. 种子数据的 name、category、rank、description、duties 字段不可被 LLM 提取覆盖
3. LLM 提取只能在种子数据基础上「追加」：新增别名、新增从未出现的官职
4. 种子数据之间的层级关系通过 parent_title_uid 字段表达

层级结构说明：
fanzhen.html 中 cat='inst' 的节点是「机构」（如"节度使府"、"文职幕府"），
它们作为层级中间节点存在，category 设为"机构"。
实际层级：
  节度使 (藩镇)
  ├── 副节度使 (藩镇)
  ├── 留后 (藩镇) ← 节度使预备官职
  └── 节度使府 (机构)
       ├── 文职幕府 (机构)
       │    └── 行军司马, 判官, 掌书记 ...
       ├── 武职军府 (机构)
       │    └── 都知兵马使, 都虞候 ...
       ├── 州县系统 (机构)
       │    └── 刺史 → 县令
       └── 监察财务 (机构)
            └── 观察处置使, 支度使, 营田使
"""

from models.entities import OfficialTitle

# ═══════════════════════════════════════════════════════════════
# fanzhen.html cat 到 OfficialTitle.category 的映射
# ═══════════════════════════════════════════════════════════════
#   top      (最高长官)  → 藩镇
#   civil    (文职幕僚)  → 文职
#   military (武职将领)  → 军职
#   local    (州县官员)  → 地方
#   supervise(监察财务)  → 监察
#   inst     (机构)      → 机构

SEED_OFFICIAL_TITLES: list[OfficialTitle] = [

    # ━━━━━━━━━━━━━━━ 藩镇最高长官 ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_jiedushi",
        name="节度使",
        aliases=["大使", "节帅", "帅臣"],
        category="藩镇",
        rank="从二品/正三品",
        description="藩镇最高军政长官，统领一镇军务、民政、财政。唐睿宗景云二年（711）始设，安史之乱后成为地方实际统治者。",
        duties="统辖军镇、任免僚属、征收赋税、指挥作战",
        source="seed",
        parent_title_uid=None,
    ),
    OfficialTitle(
        uid="title_fujiedushi",
        name="副节度使",
        aliases=["副大使", "副帅"],
        category="藩镇",
        rank="从三品",
        description="节度使副职，协助处理军政事务。常由节度使心腹或朝廷指派人员担任。",
        duties="协助节度使处理政务、代行节度使职权",
        source="seed",
        parent_title_uid="title_jiedushi",
    ),
    OfficialTitle(
        uid="title_liuhou",
        name="留后",
        aliases=["节度留后", "留后使", "知留后"],
        category="藩镇",
        rank="从三品",
        description="节度使的预备官职。当节度使出征、入朝或去世时，由留后暂代节度使职权。"
                    "常为节度使继任的过渡身份，朝廷正式任命前先以'留后'名义主持军政。",
        duties="代行节度使职权、暂掌军镇、等待朝廷正式任命",
        source="seed",
        parent_title_uid="title_jiedushi",
    ),

    # ━━━━━━━━━━━━━━━ 机构节点（层级中间节点） ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_jiedushifu",
        name="节度使府",
        aliases=["帅府", "节府", "军府"],
        category="机构",
        rank=None,
        description="藩镇最高军政机构，统领一镇军务、民政、财政。",
        duties=None,
        source="seed",
        parent_title_uid="title_jiedushi",
    ),
    OfficialTitle(
        uid="title_civil_bureau",
        name="文职幕府",
        aliases=["幕府"],
        category="机构",
        rank=None,
        description="负责军府政务、文书、司法等事务的文职机构。",
        duties=None,
        source="seed",
        parent_title_uid="title_jiedushifu",
    ),
    OfficialTitle(
        uid="title_military_bureau",
        name="武职军府",
        aliases=["军府"],
        category="机构",
        rank=None,
        description="负责军事指挥、训练、军纪的武职机构。",
        duties=None,
        source="seed",
        parent_title_uid="title_jiedushifu",
    ),
    OfficialTitle(
        uid="title_local_bureau",
        name="州县系统",
        aliases=[],
        category="机构",
        rank=None,
        description="地方行政系统，掌管州县民政。",
        duties=None,
        source="seed",
        parent_title_uid="title_jiedushifu",
    ),
    OfficialTitle(
        uid="title_supervise_bureau",
        name="监察财务",
        aliases=[],
        category="机构",
        rank=None,
        description="负责监察、财政、屯田等事务的机构。",
        duties=None,
        source="seed",
        parent_title_uid="title_jiedushifu",
    ),

    # ━━━━━━━━━━━━━━━ 文职幕府 ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_xingjunsima",
        name="行军司马",
        aliases=["军司马", "司马"],
        category="文职",
        rank="从五品下",
        description="藩镇幕府首席文职，掌管军府日常政务、文书往来、军令传达。地位仅次于副节度使。",
        duties="掌管军府政务、起草文书、传达军令",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_panguan",
        name="判官",
        aliases=["观察判官", "支度判官"],
        category="文职",
        rank="从六品下",
        description="藩镇重要幕僚，分掌各类具体事务，如刑狱、财政、军需等。大藩可设多员。",
        duties="分掌刑狱、财政、军需等具体事务",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_zhangshuji",
        name="掌书记",
        aliases=["书记", "掌记"],
        category="文职",
        rank="从七品上",
        description="掌管节度使府文书撰写，负责奏章、檄文、书信等重要文件的起草。多由进士出身者担任。",
        duties="起草奏章、檄文、书信等重要文书",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_tuiguan",
        name="推官",
        aliases=["军推", "节度推官"],
        category="文职",
        rank="从七品下",
        description="掌管刑狱推勘，负责审理案件、调查犯罪。藩镇司法事务的主要负责人。",
        duties="审理案件、推勘刑狱、调查犯罪",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_xunguan",
        name="巡官",
        aliases=["节度巡官"],
        category="文职",
        rank="从八品上",
        description="负责巡查地方、传递命令、监察军纪。常作为节度使耳目派驻各地。",
        duties="巡查地方、传递命令、监察军纪",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_suijun",
        name="随军",
        aliases=["随军参谋"],
        category="文职",
        rank="从八品下",
        description="随军参谋，负责军中杂务、记录军功、管理档案。",
        duties="随军参谋、记录军功、管理档案",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_yaoji",
        name="要籍",
        aliases=[],
        category="文职",
        rank="从九品",
        description="掌管军府名籍、兵员名册、物资账目等。幕府基层文职。",
        duties="管理兵员名册、物资账目",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),
    OfficialTitle(
        uid="title_jinzouguan",
        name="进奏官",
        aliases=["进奏院官", "邸吏"],
        category="文职",
        rank="未入流",
        description="驻京进奏院负责人，负责藩镇与朝廷的联络、传递消息、上奏章表。",
        duties="驻京联络、传递消息、上奏章表",
        source="seed",
        parent_title_uid="title_civil_bureau",
    ),

    # ━━━━━━━━━━━━━━━ 武职军府 ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_douzhibingmashi",
        name="都知兵马使",
        aliases=["都兵马使", "兵马都使"],
        category="军职",
        rank="从四品",
        description="藩镇最高武职，统领全镇兵马，负责军事指挥。常由节度使亲信或悍将担任，权力极大。",
        duties="统领全镇兵马、指挥作战",
        source="seed",
        parent_title_uid="title_military_bureau",
    ),
    OfficialTitle(
        uid="title_bingmashi",
        name="兵马使",
        aliases=["左兵马使", "右兵马使"],
        category="军职",
        rank="从五品",
        description="统兵将领，分领各部兵马。大藩可设左、右、前、后等兵马使。",
        duties="分领各部兵马、执行作战任务",
        source="seed",
        parent_title_uid="title_douzhibingmashi",
    ),
    OfficialTitle(
        uid="title_douyuhou",
        name="都虞候",
        aliases=["虞候都使"],
        category="军职",
        rank="从五品下",
        description="掌管军纪、纠察军容、维持军法。藩镇军中执法长官，地位重要。",
        duties="掌管军纪、纠察军容、维持军法",
        source="seed",
        parent_title_uid="title_military_bureau",
    ),
    OfficialTitle(
        uid="title_yuhou",
        name="虞候",
        aliases=["军虞候"],
        category="军职",
        rank="从六品",
        description="军中执法官，协助都虞候维持军纪、巡逻警戒。",
        duties="维持军纪、巡逻警戒",
        source="seed",
        parent_title_uid="title_douyuhou",
    ),
    OfficialTitle(
        uid="title_doujiaolianshi",
        name="都教练使",
        aliases=["教练使", "都教使"],
        category="军职",
        rank="从六品上",
        description="掌管军队训练、操演武艺。负责全镇军事训练事务。",
        duties="掌管军队训练、操演武艺",
        source="seed",
        parent_title_uid="title_military_bureau",
    ),
    OfficialTitle(
        uid="title_jiaolianshi",
        name="教练使",
        aliases=[],
        category="军职",
        rank="从七品",
        description="负责具体训练事务，教授士兵武艺、阵法。",
        duties="教授武艺、训练阵法",
        source="seed",
        parent_title_uid="title_doujiaolianshi",
    ),

    # ━━━━━━━━━━━━━━━ 州县系统 ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_cishi",
        name="刺史",
        aliases=["州刺史", "太守"],
        category="地方",
        rank="从三品至从四品",
        description="州级行政长官，掌管一州民政。藩镇体制下常由节度使兼任或指派亲信担任。",
        duties="掌管一州民政、征收赋税、维护治安",
        source="seed",
        parent_title_uid="title_local_bureau",
    ),
    OfficialTitle(
        uid="title_xianling",
        name="县令",
        aliases=["县尊", "明府"],
        category="地方",
        rank="从六品上至从七品下",
        description="县级行政长官，掌管一县政务。基层行政的核心官员。",
        duties="掌管一县政务、劝课农桑、征收赋税",
        source="seed",
        parent_title_uid="title_cishi",
    ),

    # ━━━━━━━━━━━━━━━ 监察财务 ━━━━━━━━━━━━━━━

    OfficialTitle(
        uid="title_guanchashi",
        name="观察处置使",
        aliases=["观察使"],
        category="监察",
        rank="正三品",
        description="掌管监察、考核地方官员。常由节度使兼任，称\u201c节度观察处置使\u201d。",
        duties="监察地方官员、考核政绩、巡察州县",
        source="seed",
        parent_title_uid="title_supervise_bureau",
    ),
    OfficialTitle(
        uid="title_zhidushi",
        name="支度使",
        aliases=[],
        category="监察",
        rank="从三品",
        description="掌管财政收支、军需物资调配。常由节度使兼任或派判官分掌。",
        duties="掌管财政收支、调配军需物资",
        source="seed",
        parent_title_uid="title_supervise_bureau",
    ),
    OfficialTitle(
        uid="title_yingtianshi",
        name="营田使",
        aliases=[],
        category="监察",
        rank="从三品",
        description="掌管屯田事务，组织军士耕种、管理屯田收入。",
        duties="掌管屯田、组织军士耕种",
        source="seed",
        parent_title_uid="title_supervise_bureau",
    ),
]

# 种子官职 uid 集合（用于快速判断某 uid 是否为种子数据）
SEED_TITLE_UIDS: set[str] = {t.uid for t in SEED_OFFICIAL_TITLES}

# 种子官职名字集合（所有 name + aliases，用于匹配）
SEED_TITLE_ALL_NAMES: set[str] = set()
for _t in SEED_OFFICIAL_TITLES:
    SEED_TITLE_ALL_NAMES.update(_t.all_names())

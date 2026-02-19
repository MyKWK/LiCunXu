"""五代历史种子数据 - 核心人物、政权与关键事件

此文件提供一批经过人工校验的高质量种子数据，用于：
1. 初始化图谱基础框架（朝代、核心人物）
2. 作为 LLM 抽取的参考锚点（去重/消歧）
3. 确保核心历史脉络的准确性
"""

from models.entities import Dynasty, Event, Person, Place, Relation

# ═══════════════════════════════════════════════════════════════
# 五代政权
# ═══════════════════════════════════════════════════════════════

SEED_DYNASTIES = [
    Dynasty(uid="dynasty_later_liang", name="后梁", founder="朱温",
            capital="开封", start_year=907, end_year=923,
            description="朱温篡唐所建，五代第一个政权"),
    Dynasty(uid="dynasty_later_tang", name="后唐", founder="李存勖",
            capital="洛阳", start_year=923, end_year=936,
            description="李存勖灭后梁所建，沙陀族政权"),
    Dynasty(uid="dynasty_later_jin", name="后晋", founder="石敬瑭",
            capital="开封", start_year=936, end_year=947,
            description="石敬瑭借契丹兵灭后唐所建，割让燕云十六州"),
    Dynasty(uid="dynasty_later_han", name="后汉", founder="刘知远",
            capital="开封", start_year=947, end_year=951,
            description="刘知远趁契丹北撤所建，五代最短命政权"),
    Dynasty(uid="dynasty_later_zhou", name="后周", founder="郭威",
            capital="开封", start_year=951, end_year=960,
            description="郭威代汉所建，柴荣时期国力最强"),
    Dynasty(uid="dynasty_khitan", name="契丹/辽", founder="耶律阿保机",
            capital="上京", start_year=907, end_year=1125,
            description="契丹族建立的北方政权"),
    Dynasty(uid="dynasty_northern_han", name="北汉", founder="刘旻",
            capital="太原", start_year=951, end_year=979,
            description="刘崇所建，依附契丹对抗后周"),
]

# ═══════════════════════════════════════════════════════════════
# 核心人物
# ═══════════════════════════════════════════════════════════════

SEED_PERSONS = [
    # ---- 后梁 ----
    Person(uid="person_zhuwen", original_name="朱温",
           aliases=["朱全忠", "朱晃", "朱三"],
           role="皇帝", loyalty=["黄巢军", "唐", "后梁"],
           birth_year=852, death_year=912, death_cause="被其子朱友珪所杀",
           description="后梁太祖，原为黄巢部将，后降唐，最终篡唐建梁"),
    Person(uid="person_zhu_yougui", original_name="朱友珪",
           role="皇帝", loyalty=["后梁"],
           death_year=913, death_cause="被朱友贞兵变推翻后自杀",
           description="朱温第三子，弑父篡位"),
    Person(uid="person_zhu_youzhen", original_name="朱友贞",
           aliases=["朱瑱"],
           role="皇帝", loyalty=["后梁"],
           death_year=923, death_cause="后梁亡国时自杀",
           description="后梁末帝"),

    # ---- 后唐 / 李克用系 ----
    Person(uid="person_li_keyong", original_name="李克用",
           aliases=["朱邪克用"],
           role="藩镇节度使", loyalty=["唐", "河东"],
           birth_year=856, death_year=908, death_cause="病逝",
           description="沙陀族首领，唐末河东节度使，与朱温为死敌，临终留三矢遗命"),
    Person(uid="person_li_cunxu", original_name="李存勖",
           aliases=["李亚子"],
           role="皇帝", loyalty=["河东", "后唐"],
           birth_year=885, death_year=926, death_cause="兴教门之变中被杀",
           description="后唐庄宗，李克用之子，灭后梁建后唐，后期沉溺伶人荒废朝政"),
    Person(uid="person_li_siyuan", original_name="李嗣源",
           aliases=["邈佶烈"],
           role="皇帝", loyalty=["河东", "后唐"],
           birth_year=867, death_year=933, death_cause="病逝",
           description="后唐明宗，李克用养子，兵变入洛阳继位，五代少有的贤明之主"),
    Person(uid="person_li_congke", original_name="李从珂",
           role="皇帝", loyalty=["后唐"],
           birth_year=885, death_year=937, death_cause="自焚而死",
           description="后唐末帝，李嗣源养子，起兵夺位后被石敬瑭联合契丹灭亡"),
    Person(uid="person_li_cunxiao", original_name="李存孝",
           aliases=["安敬思"],
           role="将领", loyalty=["河东"],
           death_year=894, death_cause="被李克用车裂处死",
           description="李克用义子，号称五代第一猛将，后因谋反被杀"),

    # ---- 后晋 ----
    Person(uid="person_shi_jingtang", original_name="石敬瑭",
           role="皇帝", loyalty=["后唐", "后晋"],
           birth_year=892, death_year=942, death_cause="忧惧而死",
           description="后晋高祖，李嗣源女婿，以割让燕云十六州为代价借契丹兵灭后唐"),
    Person(uid="person_shi_chonggui", original_name="石重贵",
           role="皇帝", loyalty=["后晋"],
           death_year=974, death_cause="客死契丹",
           description="后晋出帝，石敬瑭侄，对契丹强硬导致亡国"),

    # ---- 后汉 ----
    Person(uid="person_liu_zhiyuan", original_name="刘知远",
           role="皇帝", loyalty=["后唐", "后晋", "后汉"],
           birth_year=895, death_year=948, death_cause="病逝",
           description="后汉高祖，趁契丹北撤在太原称帝"),
    Person(uid="person_liu_chengyou", original_name="刘承祐",
           role="皇帝", loyalty=["后汉"],
           death_year=951, death_cause="被杀",
           description="后汉隐帝，诛杀权臣导致郭威兵变"),

    # ---- 后周 ----
    Person(uid="person_guo_wei", original_name="郭威",
           role="皇帝", loyalty=["后汉", "后周"],
           birth_year=904, death_year=954, death_cause="病逝",
           description="后周太祖，因家属被刘承祐杀害而起兵代汉"),
    Person(uid="person_chai_rong", original_name="柴荣",
           aliases=["郭荣"],
           role="皇帝", loyalty=["后周"],
           birth_year=921, death_year=959, death_cause="北伐途中病逝",
           description="后周世宗，郭威养子，五代最杰出的君主，推行全面改革"),
    Person(uid="person_zhao_kuangyin", original_name="赵匡胤",
           role="将领", loyalty=["后周", "北宋"],
           birth_year=927, death_year=976, death_cause="烛影斧声（存疑）",
           description="后周殿前都点检，陈桥兵变黄袍加身建立北宋"),

    # ---- 契丹 ----
    Person(uid="person_yelv_abaoji", original_name="耶律阿保机",
           role="皇帝", loyalty=["契丹/辽"],
           birth_year=872, death_year=926, death_cause="病逝",
           description="契丹开国皇帝，统一契丹八部"),
    Person(uid="person_yelv_deguang", original_name="耶律德光",
           role="皇帝", loyalty=["契丹/辽"],
           birth_year=902, death_year=947, death_cause="北归途中病逝",
           description="辽太宗，助石敬瑭灭后唐，后灭后晋入开封"),

    # ---- 其他关键人物 ----
    Person(uid="person_liu_min", original_name="刘旻",
           aliases=["刘崇"],
           role="皇帝", loyalty=["后汉", "北汉"],
           death_year=954, death_cause="高平之战惨败后忧死",
           description="北汉开国皇帝，刘知远弟，后周灭后汉后割据太原"),
    Person(uid="person_feng_dao", original_name="冯道",
           role="大臣", loyalty=["后唐", "后晋", "契丹/辽", "后汉", "后周"],
           birth_year=882, death_year=954, death_cause="病逝",
           description="历仕四朝十帝的宰相，人称'不倒翁'"),
    Person(uid="person_jing_yanguang", original_name="景延广",
           role="将领", loyalty=["后晋"],
           death_year=947, death_cause="契丹灭晋后自杀",
           description="后晋将领，主张对契丹强硬"),
]

# ═══════════════════════════════════════════════════════════════
# 关键地点
# ═══════════════════════════════════════════════════════════════

SEED_PLACES = [
    Place(uid="place_kaifeng", name="开封", modern_name="河南开封", description="后梁/后晋/后汉/后周都城"),
    Place(uid="place_luoyang", name="洛阳", modern_name="河南洛阳", description="后唐都城"),
    Place(uid="place_taiyuan", name="太原", modern_name="山西太原", description="河东藩镇/北汉都城"),
    Place(uid="place_youzhou", name="幽州", modern_name="北京", description="燕云十六州核心，契丹南下要冲"),
    Place(uid="place_chenqiao", name="陈桥驿", modern_name="河南封丘陈桥镇", description="赵匡胤黄袍加身之地"),
    Place(uid="place_gaoping", name="高平", modern_name="山西高平", description="后周大败北汉+契丹联军之地"),
]

# ═══════════════════════════════════════════════════════════════
# 关键事件
# ═══════════════════════════════════════════════════════════════

SEED_EVENTS = [
    Event(uid="event_zhuwen_usurp", name="朱温篡唐", event_type="皇位更替",
          year=907, location="开封", participants=["朱温"],
          outcome="唐朝灭亡，后梁建立",
          description="朱温逼迫唐哀帝禅位，建立后梁，定都开封"),
    Event(uid="event_three_arrows", name="三矢遗命", event_type="其他",
          year=908, location="太原", participants=["李克用", "李存勖"],
          outcome="李存勖继承父志",
          description="李克用临终赐李存勖三支箭，嘱其消灭三大仇敌：朱温、刘仁恭、契丹"),
    Event(uid="event_zhu_yougui_patricide", name="朱友珪弑父", event_type="政变",
          year=912, location="洛阳", participants=["朱友珪", "朱温"],
          outcome="朱温被杀，朱友珪即位",
          description="朱友珪得知朱温欲传位朱友文，遂率兵入宫弑父篡位"),
    Event(uid="event_destroy_later_liang", name="后唐灭后梁", event_type="战争",
          year=923, location="开封", participants=["李存勖", "朱友贞"],
          outcome="后梁灭亡，后唐统一北方",
          description="李存勖奇袭开封，朱友贞自杀，后梁亡"),
    Event(uid="event_xingjiaomen", name="兴教门之变", event_type="政变",
          year=926, location="洛阳", participants=["李存勖"],
          outcome="李存勖被杀",
          description="伶人郭从谦发动兵变，李存勖中流矢而亡"),
    Event(uid="event_li_siyuan_seize", name="李嗣源入洛阳", event_type="皇位更替",
          year=926, location="洛阳", participants=["李嗣源"],
          outcome="李嗣源即位为后唐明宗",
          description="兴教门之变后，李嗣源率军入洛阳即帝位"),
    Event(uid="event_shi_jingtang_rebellion", name="石敬瑭联契丹灭后唐", event_type="战争",
          year=936, location="太原", participants=["石敬瑭", "耶律德光", "李从珂"],
          outcome="后唐灭亡，后晋建立，燕云十六州割让契丹",
          description="石敬瑭以称臣、割让燕云十六州为条件借契丹兵灭后唐"),
    Event(uid="event_khitan_destroy_jin", name="契丹灭后晋", event_type="战争",
          year=947, location="开封", participants=["耶律德光", "石重贵"],
          outcome="后晋灭亡，耶律德光入开封称帝",
          description="石重贵对契丹强硬，契丹大举南下灭后晋"),
    Event(uid="event_liu_zhiyuan_found", name="刘知远建后汉", event_type="皇位更替",
          year=947, location="太原", participants=["刘知远"],
          outcome="后汉建立",
          description="契丹北撤后，刘知远在太原称帝建后汉"),
    Event(uid="event_guo_wei_rebellion", name="郭威兵变代汉", event_type="政变",
          year=951, location="开封", participants=["郭威", "刘承祐"],
          outcome="后汉灭亡，后周建立",
          description="刘承祐诛杀权臣，郭威起兵反叛，刘承祐被杀，郭威建后周"),
    Event(uid="event_gaoping_battle", name="高平之战", event_type="战争",
          year=954, location="高平", participants=["柴荣", "刘旻", "赵匡胤"],
          outcome="后周大胜，北汉势力大衰",
          description="柴荣即位后首战，大败北汉与契丹联军，赵匡胤此战崭露头角"),
    Event(uid="event_chenqiao_mutiny", name="陈桥兵变", event_type="政变",
          year=960, location="陈桥驿", participants=["赵匡胤"],
          outcome="后周灭亡，北宋建立",
          description="赵匡胤在陈桥驿被部下黄袍加身，回师开封迫周恭帝禅位"),
]

# ═══════════════════════════════════════════════════════════════
# 核心关系（用人名而非 uid）
# ═══════════════════════════════════════════════════════════════

SEED_RELATIONS = [
    # 亲族
    Relation(source="李克用", target="李存勖", relation_type="FATHER_OF", description="李克用之子李存勖"),
    Relation(source="朱温", target="朱友珪", relation_type="FATHER_OF", description="朱温之子朱友珪"),
    Relation(source="朱温", target="朱友贞", relation_type="FATHER_OF", description="朱温之子朱友贞"),
    Relation(source="石敬瑭", target="石重贵", relation_type="FATHER_OF", description="石敬瑭之侄石重贵（以子侄关系继位）"),
    Relation(source="刘知远", target="刘承祐", relation_type="FATHER_OF", description="刘知远之子刘承祐"),
    Relation(source="刘知远", target="刘旻", relation_type="SIBLING", description="刘知远之弟刘旻"),

    # 义子（重点关系）
    Relation(source="李克用", target="李嗣源", relation_type="ADOPTED_SON", description="李克用养子李嗣源"),
    Relation(source="李克用", target="李存孝", relation_type="ADOPTED_SON", description="李克用义子李存孝，十三太保之一"),
    Relation(source="李嗣源", target="李从珂", relation_type="ADOPTED_SON", description="李嗣源养子李从珂"),
    Relation(source="郭威", target="柴荣", relation_type="ADOPTED_SON", description="郭威养子柴荣（原为郭威妻侄）"),

    # 背叛
    Relation(source="朱温", target="李克用", relation_type="BETRAYED", year=884,
             description="朱温原与李克用同为唐将，上源驿之变后反目成仇"),
    Relation(source="石敬瑭", target="李从珂", relation_type="BETRAYED", year=936,
             description="石敬瑭叛后唐，联合契丹灭后唐"),

    # 杀害
    Relation(source="朱友珪", target="朱温", relation_type="KILLED", year=912,
             description="朱友珪弑父朱温"),
    Relation(source="李克用", target="李存孝", relation_type="KILLED", year=894,
             description="李克用处死义子李存孝（车裂）"),

    # 篡位/更替
    Relation(source="朱温", target="后梁", relation_type="REPLACED", year=907, description="朱温篡唐建后梁"),
    Relation(source="李存勖", target="后梁", relation_type="REPLACED", year=923, description="李存勖灭后梁建后唐"),
    Relation(source="石敬瑭", target="后唐", relation_type="REPLACED", year=936, description="石敬瑭灭后唐建后晋"),
    Relation(source="刘知远", target="后晋", relation_type="REPLACED", year=947, description="刘知远趁契丹北撤建后汉"),
    Relation(source="郭威", target="后汉", relation_type="REPLACED", year=951, description="郭威兵变代汉建后周"),
    Relation(source="赵匡胤", target="后周", relation_type="REPLACED", year=960, description="赵匡胤陈桥兵变建北宋"),

    # 效力
    Relation(source="赵匡胤", target="柴荣", relation_type="SERVED", description="赵匡胤为柴荣殿前都点检"),
    Relation(source="石敬瑭", target="李嗣源", relation_type="SERVED", description="石敬瑭为李嗣源女婿兼部将"),

    # 继位
    Relation(source="李嗣源", target="李存勖", relation_type="SUCCEEDED", year=926,
             description="李嗣源于兴教门之变后继位"),
    Relation(source="柴荣", target="郭威", relation_type="SUCCEEDED", year=954,
             description="柴荣继郭威之位"),
]

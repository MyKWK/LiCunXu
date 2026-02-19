# 五代十国知识图谱与智能问答系统

**Five Dynasties KG-RAG System**

基于 LLM 驱动的自动化知识抽取，从历史著作中构建结构化知识图谱，结合 Graph RAG 实现智能历史问答。

---

## 目录

- [一、项目概述](#一项目概述)
- [二、功能特性](#二功能特性)
- [三、快速启动](#三快速启动)
- [四、系统架构](#四系统架构)
- [五、技术选型](#五技术选型)
- [六、数据结构](#六数据结构)
- [七、核心机制详解](#七核心机制详解)
- [八、API 接口](#八api-接口)
- [九、CLI 命令行工具](#九cli-命令行工具)
- [十、项目目录结构](#十项目目录结构)

---

## 一、项目概述

### 背景

五代十国（907—960）是中国历史上人物最密集、改名最频繁、关系最混乱的时期之一。仅中原五代加十国，涉及的有名有姓的历史人物就超过上千人，其中大量人物存在**赐姓改名**（如元行钦被赐名李绍荣）、**即位改名**（如朱温→朱全忠→朱晃）、**胡汉双名**（如李嗣源本名邈佶烈）等现象，传统的手工整理方式几乎无法应对。

### 目标

本项目通过 **LLM 驱动的自动化知识抽取**，从两部五代十国通俗历史著作中，逐段阅读、理解并提取出完整的结构化知识图谱，涵盖人物、政权、事件、地点及其错综复杂的关系网络，并提供交互式可视化和智能问答能力。

### 数据来源

| 书籍 | 文件 | 大小 | 内容覆盖 |
|------|------|------|----------|
| 《五代十国全史》（全9卷） | `five_kindom.txt` | 6.41 MB | 从黄巢起义到赵匡胤陈桥兵变的完整叙事 |
| 《帝国的崩裂：细说五代十国史》 | `splited_empire.txt` | 711 KB | 以政权为线索的五代十国通史 |

### 当前图谱规模

| 指标 | 数量 |
|------|------|
| 人物（Person） | 3,432 |
| 事件（Event） | 7,013 |
| 地点（Place） | 2,578 |
| 政权（Dynasty） | 238 |
| 关系总数 | 36,000+ |
| 关系类型 | 200+ 种 |

---

## 二、功能特性

### 1. 知识图谱交互式可视化

- **力导向图布局**：基于 vis-network 物理引擎，人物、事件、政权等节点以不同颜色和形状呈现
- **稀疏化显示**：动态调整斥力、弹簧长度等参数，避免节点过于密集；事件节点限制最多展示 30 个
- **人物关系网络**：输入人名查看其关系图谱，支持按关系深度（1-3 层）展开
- **皇位更替链**：一键查看五代皇位传承的完整链条
- **节点交互**：
  - **单击人物节点** → 右侧详情面板展示：完整信息（别名、势力、生卒、死因、描述）、人物关系列表（可点击跳转）、参与事件列表（按时间排序）
  - **单击事件节点** → 右侧详情面板展示：事件详情（年份、地点、结果、描述）、参与人物、书中原文片段（自动搜索并高亮关键词）
  - **双击人物节点** → 以该人物为中心重新加载图谱

### 2. 智能问答（Graph RAG）

使用自然语言提问，系统自动：

1. **预定义查询匹配**：正则识别义子查询、皇位更替、家族树、人物关系等常见问题模式，直接调用图谱查询
2. **LLM 生成 Cypher**：将自然语言问题转化为 Neo4j Cypher 查询语句
3. **安全执行**：拦截所有写入操作（CREATE/DELETE/SET），仅允许只读查询
4. **智能回答**：将图谱查询结果交给 DeepSeek LLM，由"五代十国历史学家"角色生成深度解读
5. **降级回答**：图谱无结果时，LLM 基于自身历史知识回答，并明确标注数据来源

示例问答：
- "李克用有哪些义子？" → 自动查询 ADOPTED_SON 关系并列举
- "朱温是怎么当上皇帝的？" → 从图谱中检索相关事件并组织回答
- "五代皇帝的更替顺序是什么？" → 展示完整的皇位继承链
- "李存勖和李嗣源是什么关系？" → 查询两人之间的所有关系路径

### 3. 人物搜索

- 支持按本名或别名搜索
- 搜索结果直接展示关系网络图

### 4. 事件详情与原文溯源

- 点击任意事件节点，系统自动从两本原始书籍中搜索相关原文片段
- 展示事件的参与人物、时间、地点、结果等结构化信息
- 原文片段中高亮匹配的关键词

### 5. 命令行工具

提供完整的 CLI 管理能力：数据摄入、种子加载、命令行问答、人物搜索、图谱统计等。

---

## 三、快速启动

### 环境要求

- **Python** 3.11+
- **Neo4j Community Edition** 5.x（需先安装并启动）
- **网络**：需要访问 DeepSeek API（用于知识库问答和 LLM 总结）

### 环境变量配置

本项目通过 `.env` 文件管理所有敏感配置（API 密钥等），`.env` 已在 `.gitignore` 中，**不会被提交到 Git 仓库**。

项目提供了 `.env.example` 作为模板，包含所有可配置项及说明：

| 变量名 | 说明 | 是否必填 |
|--------|------|----------|
| `NEO4J_URI` | Neo4j 连接地址 | 可选（默认 `bolt://localhost:7687`） |
| `NEO4J_USER` | Neo4j 用户名 | 可选（默认 `neo4j`） |
| `NEO4J_PASSWORD` | Neo4j 密码 | 推荐配置 |
| `LLM_API_KEY` | 维纳斯平台 API Key（数据摄入阶段使用） | 仅摄入时需要 |
| `LLM_API_BASE` | 维纳斯平台 API 地址 | 仅摄入时需要 |
| `LLM_MODEL_NAME` | 维纳斯平台模型名 | 可选 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（问答 + LLM 总结） | **必填** |
| `DEEPSEEK_API_BASE` | DeepSeek API 地址 | 可选（默认 `https://api.deepseek.com`） |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | 可选（默认 `deepseek-chat`） |
| `EMBEDDING_MODEL_NAME` | 本地 Embedding 模型 | 可选（默认 `BAAI/bge-small-zh-v1.5`） |
| `APP_HOST` / `APP_PORT` | Web 服务地址和端口 | 可选（默认 `0.0.0.0:8000`） |

> **安全提醒**：请勿将 `.env` 文件或任何包含真实 API 密钥的文件提交到版本控制系统。

### 安装步骤

```bash
# 1. 克隆项目
cd /path/to/LiCunXu

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. 配置环境变量
#    复制示例文件并填入真实密钥（.env 已被 .gitignore 忽略，不会上传到 Git）
cp .env.example .env

#    编辑 .env，填入你的密钥：
#    - DEEPSEEK_API_KEY: DeepSeek 平台申请的 API Key（必填，用于问答和总结）
#    - NEO4J_PASSWORD: 你的 Neo4j 密码
#    - LLM_API_KEY: 维纳斯平台密钥（仅数据摄入阶段需要）
vi .env
```

### 启动 Neo4j

```bash
# macOS (Homebrew)
brew services start neo4j

# 或者使用 Docker
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5
```

### 数据准备与摄入

```bash
# 加载种子数据（人工校验的朝代框架与核心人物）
python cli.py seed --clear

# 全量知识摄入（LLM 逐块抽取 + 实时写入 Neo4j，约 30+ 小时）
nohup python cli.py ingest --no-resume > logs/ingest_full.log 2>&1 &

# 监控摄入进度
tail -f logs/ingest_full.log
python cli.py stats

# 或从已保存的抽取结果恢复（跳过 LLM 步骤，快速重建图谱）
python cli.py ingest-saved
```

### 启动 Web 服务

```bash
# 方式一：通过入口文件启动
python main.py

# 方式二：通过 CLI 启动
python cli.py serve
```

启动后访问 **http://localhost:8000** 即可使用 Web 界面。

### 快速验证

```bash
# 查看图谱统计
python cli.py stats

# 命令行问答测试
python cli.py ask "李存勖是谁？"

# 搜索人物
python cli.py search "朱温"
```

---

## 四、系统架构

### 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                          用户界面层                                │
│  ┌──────────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Web 图谱可视化    │  │  智能问答面板  │  │   CLI 命令行工具   │  │
│  │  (vis-network)    │  │  (对话式交互)  │  │    (cli.py)       │  │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬──────────┘  │
├───────────┼────────────────────┼───────────────────┼─────────────┤
│                    Web API 层 (FastAPI + Uvicorn)                 │
│                      api/routes.py                               │
│   ┌──────────┬──────────┬───────────┬──────────┬────────────┐   │
│   │ 图谱查询  │ 人物详情  │ 事件详情   │ 问答接口  │ 可视化页面  │   │
│   │ /api/graph│ /person/ │ /event/   │ /api/ask │ /          │   │
│   └────┬─────┴────┬─────┴─────┬─────┴────┬─────┴────────────┘   │
├────────┼──────────┼───────────┼──────────┼───────────────────────┤
│                     智能问答层 (Graph RAG)                         │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                    rag/engine.py                        │     │
│  │                                                         │     │
│  │  1. 预定义查询匹配（义子/皇位更替/家族树/人物关系）         │     │
│  │  2. 维纳斯 LLM → 生成 Cypher 查询                       │     │
│  │  3. 安全检查 → 执行 Cypher → 获取图谱数据                 │     │
│  │  4. DeepSeek LLM → 历史学家角色 → 深度解读               │     │
│  │  5. 降级模式：图谱无数据时基于 LLM 通用知识回答            │     │
│  └──────────┬──────────────────────────┬───────────────────┘     │
│             │                          │                         │
├─────────────┼──────────────────────────┼─────────────────────────┤
│       图谱服务层                    LLM 服务层                     │
│  ┌──────────┴──────────┐   ┌───────────┴──────────────────┐     │
│  │   graph/crud.py     │   │   config/llm_client.py       │     │
│  │                     │   │                               │     │
│  │  · 智能人物合并     │   │  · VenusLLM (内部任务)        │     │
│  │    (merge_person)   │   │    - Cypher 生成              │     │
│  │  · 名字/别名解析    │   │    - 数据摄入/清洗            │     │
│  │  · 关系增删改查     │   │                               │     │
│  │  · 通用称号黑名单   │   │  · DeepSeekLLM (用户问答)     │     │
│  │  · 全节点类型 CRUD  │   │    - 历史学家 Prompt          │     │
│  └──────────┬──────────┘   │    - 图谱数据深度解读         │     │
│             │              └───────────┬──────────────────┘     │
│       ┌─────┴─────┐          ┌────────┴────────┐               │
│       │  Neo4j DB  │          │ Venus / DeepSeek │               │
│       │ (图数据库)  │          │   (LLM API)      │               │
│       └───────────┘          └─────────────────┘               │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                        数据摄入层                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              ingestion/pipeline.py                       │   │
│  │                                                          │   │
│  │  books/*.txt                                             │   │
│  │      ↓  text_processor.py（去目录 → 章节切分 → 分块）     │   │
│  │  TextChunk (1500字/块, 200字重叠)                         │   │
│  │      ↓  extractor.py（LLM 改名感知提示词 + 增量名单反馈） │   │
│  │  ExtractionResult (人物/政权/事件/地点/关系)               │   │
│  │      ↓  crud.py（名字集合匹配 → 智能合并 → 实时写入）     │   │
│  │  Knowledge Graph                                         │   │
│  │                                                          │   │
│  │  特性：逐块提取+实时入库 / 断点续传 / 每10块checkpoint    │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 数据摄入流程

```
原始文本 (.txt)
    │
    ▼
text_processor.py ─── 智能去除目录 → 按章节切分 → 按句子边界分块（1500字/块, 200字重叠）
    │
    ▼
extractor.py ───────── 构建改名感知提示词 → 调用维纳斯 LLM API → 解析 JSON 响应
    │                   ↑
    │                   │ 每 20 块从 Neo4j 拉取已入库人物名单，
    │                   │ 注入下一轮提示词做名字对齐
    ▼
pipeline.py ────────── 逐块处理：提取 → 实时写入 Neo4j → 记录进度
    │                   · merge_person() 基于名字/别名智能合并
    │                   · resolve_node_uid() 跨类型名字解析
    │                   · 每 10 块保存 checkpoint（支持断点续传）
    ▼
Neo4j 知识图谱
```

### 问答流程

```
用户提问（Web / CLI）
    │
    ▼
rag/engine.py
    ├── 1. 预定义查询匹配（正则）
    │       → 义子查询 / 皇位更替 / 家族树 / 人物关系
    │       → 命中则直接调用 graph_crud 查询
    │
    ├── 2. LLM 生成 Cypher（维纳斯 Qwen3-235B）
    │       → 输入：用户问题 + 完整图谱 Schema
    │       → 输出：可执行的 Cypher 查询语句
    │
    ├── 3. 安全检查 + 执行
    │       → 拦截 CREATE/DELETE/SET/MERGE 等写入操作
    │       → 执行 Cypher → 获取图谱数据
    │
    ├── 4. DeepSeek 生成回答
    │       → 系统角色：五代十国资深历史学家
    │       → 输入：用户问题 + 图谱检索结果
    │       → 输出：结合图谱数据与历史知识的深度回答
    │
    └── 5. 降级回答（图谱无结果时）
            → DeepSeek 基于通用历史知识回答
            → 明确标注"基于通用知识，非图谱数据"
    │
    ▼
返回结构化响应 { question, answer, cypher, cypher_error, graph_data }
```

---

## 五、技术选型

### 技术栈总览

| 层级 | 技术 | 版本 | 选型理由 |
|------|------|------|----------|
| **图数据库** | Neo4j Community Edition | 5.x | 原生图存储，Cypher 查询语言对复杂关系查询表现优异；全文索引支持中文搜索 |
| **LLM（内部任务）** | Qwen3-235B（维纳斯平台） | — | 用于知识抽取和 Cypher 生成，大参数模型在中文历史文本理解上表现出色 |
| **LLM（用户问答）** | DeepSeek Chat | — | 用于面向用户的知识库问答，响应速度快，中文生成质量高 |
| **后端框架** | FastAPI + Uvicorn | 0.115+ | 异步高性能，自动生成 OpenAPI 文档，类型提示完善 |
| **数据建模** | Pydantic + Pydantic-Settings | 2.10+ | 强类型实体建模、配置管理、自动数据校验和序列化 |
| **前端可视化** | vis-network (vis.js) | 9.1 | 成熟的图可视化库，力导向布局物理引擎可调参，交互事件丰富 |
| **中文分词** | jieba | 0.42+ | 轻量级中文分词，用于辅助搜索和关键词提取 |
| **日志系统** | loguru | 0.7+ | 开箱即用的结构化日志，支持文件轮转和彩色终端输出 |
| **语言** | Python | 3.11+ | 全栈统一语言，LLM 生态完善 |

### 为什么选择 Neo4j 而非关系型数据库？

五代十国历史数据的核心特征是**关系网络极其复杂**：

- 一个人物可能同时与数十个人存在不同类型的关系（父子、君臣、敌对、背叛、联姻等）
- 关系具有方向性和多样性：A 背叛 B、B 杀害 C、C 是 A 的义子
- 查询需求以"关系路径"为主：某人的所有义子、两人之间的关系链、皇位更替链等

这些需求在关系型数据库中需要大量 JOIN 操作，而 Neo4j 的原生图遍历性能远优于此。单条 Cypher 查询即可完成多跳关系遍历：

```cypher
-- 查询李克用到李存勖之间的所有关系路径
MATCH path = (a:Person)-[*1..3]-(b:Person)
WHERE a.original_name = '李克用' AND b.original_name = '李存勖'
RETURN path
```

### 为什么使用双 LLM 架构？

| 功能 | 使用的 LLM | 原因 |
|------|-----------|------|
| 知识抽取（NER + RE） | 维纳斯 Qwen3-235B | 需要超大参数模型理解复杂历史语境中的人物身份和关系 |
| Cypher 查询生成 | 维纳斯 Qwen3-235B | Cypher 语法生成需要强大的代码理解能力 |
| 用户问答回答 | DeepSeek Chat | 面向终端用户，需要快速响应和高质量中文生成 |
| 数据清洗（别名审查） | 维纳斯 Qwen3-235B | 需要大参数模型的历史知识来判断别名正确性 |

---

## 六、数据结构

### 实体模型（Pydantic Schema）

所有实体定义在 `models/entities.py`，使用 Pydantic BaseModel 实现强类型约束：

#### Person（人物）

```python
class Person(BaseModel):
    uid: str              # 唯一标识符，格式 person_最常用拼音名
    original_name: str    # 最常用名字
    aliases: list[str]    # 所有别名/赐名/曾用名/本名
    role: str             # 角色：皇帝/将领/大臣/宦官/叛将/藩镇节度使/后妃/文人/僧侣/其他
    loyalty: list[str]    # 归属势力列表（按时间顺序）
    birth_year: int|None  # 出生年份
    death_year: int|None  # 死亡年份
    death_cause: str|None # 死因
    description: str      # 简要描述
```

#### Dynasty（政权）

```python
class Dynasty(BaseModel):
    uid: str              # 唯一标识符
    name: str             # 政权名称
    founder: str|None     # 建国者
    capital: str|None     # 都城
    start_year: int|None  # 起始年份
    end_year: int|None    # 终结年份
    description: str      # 描述
```

#### Event（事件）

```python
class Event(BaseModel):
    uid: str              # 唯一标识符
    name: str             # 事件名称
    event_type: str       # 类型：战争/政变/皇位更替/结盟/背叛事件/其他
    year: int|None        # 发生年份
    location: str|None    # 发生地点
    participants: list[str]  # 参与者名单
    outcome: str|None     # 结果
    description: str      # 描述
```

#### Place（地点）

```python
class Place(BaseModel):
    uid: str              # 唯一标识符
    name: str             # 古地名
    modern_name: str|None # 对应今地名
    description: str      # 描述
```

#### Relation（关系）

```python
class Relation(BaseModel):
    source: str           # 源节点名字
    target: str           # 目标节点名字
    relation_type: str    # 关系类型
    year: int|None        # 发生年份
    description: str      # 关系描述
```

### Neo4j 图谱 Schema

#### 节点标签与约束

```cypher
-- 唯一性约束（保证 uid 不重复）
CREATE CONSTRAINT person_uid IF NOT EXISTS FOR (p:Person) REQUIRE p.uid IS UNIQUE
CREATE CONSTRAINT dynasty_uid IF NOT EXISTS FOR (d:Dynasty) REQUIRE d.uid IS UNIQUE
CREATE CONSTRAINT event_uid IF NOT EXISTS FOR (e:Event) REQUIRE e.uid IS UNIQUE
CREATE CONSTRAINT place_uid IF NOT EXISTS FOR (pl:Place) REQUIRE pl.uid IS UNIQUE

-- 索引（加速查询）
CREATE INDEX person_name_idx IF NOT EXISTS FOR (p:Person) ON (p.original_name)

-- 全文索引（支持中文模糊搜索）
CREATE FULLTEXT INDEX person_fulltext_index IF NOT EXISTS
    FOR (p:Person) ON EACH [p.original_name, p.description]
CREATE FULLTEXT INDEX event_fulltext_index IF NOT EXISTS
    FOR (e:Event) ON EACH [e.name, e.description]
```

#### 主要关系类型

| 类别 | 关系类型 | 方向 | 说明 | 数量 |
|------|---------|------|------|------|
| **事件参与** | `PARTICIPATED_IN` | Person → Event | 人物参与事件 | 23,595 |
| **军事** | `SUBORDINATE` | Person → Person | 上下级 | 1,643 |
| | `COMMANDED` | Person → Person | 统帅/指挥 | 1,378 |
| **对抗** | `RIVAL` | Person ↔ Person | 对手/敌对 | 1,351 |
| | `KILLED` | Person → Person | 杀害 | 1,135 |
| | `BETRAYED` | Person → Person | 背叛 | 905 |
| | `SURRENDERED_TO` | Person → Person | 投降 | 919 |
| **政治** | `SERVED` | Person → Person/Dynasty | 效力 | 1,206 |
| | `ADVISOR` | Person → Person | 谋臣 | 771 |
| | `SUCCEEDED` | Person → Person | 继位 | 469 |
| | `REPLACED` | Person → Dynasty | 篡位/更替 | 216 |
| **外交** | `ALLIED_WITH` | Person ↔ Person | 结盟 | 721 |
| **亲族** | `SPOUSE` | Person ↔ Person | 夫妻 | 746 |
| | `SIBLING` | Person ↔ Person | 兄弟姐妹 | 585 |
| | `FATHER_OF` | Person → Person | 父子 | 457 |
| | `ADOPTED_SON` | Person → Person | 义子 | 220 |
| | `MOTHER_OF` | Person → Person | 母子 | 76 |
| **建国** | `FOUNDED` | Person → Dynasty | 建国 | 9 |

> 注：关系类型由 LLM 在摄入过程中动态生成，不限于上表，实际共 200+ 种。

### 数据中间产物

摄入过程中产生的文件存储在 `data/processed/` 下：

| 文件 | 说明 |
|------|------|
| `chunks.json` (8.3 MB) | 文本预处理后的全部文本块（1790 块） |
| `extraction_results.json` (21.4 MB) | 最终的全量 LLM 抽取结果 |
| `extraction_results_checkpoint_*.json` | 每 10 块保存一次的增量检查点 |
| `progress.json` | 断点续传进度文件 |

---

## 七、核心机制详解

### 改名智能合并 — 多名归一

五代十国时期，同一人物在不同时期、不同语境下可能有截然不同的称谓：

| 真实人物 | 可能出现的称呼 | 来源 |
|---------|-------------|------|
| **李存勖** | 李存勖、晋王、后唐庄宗、后唐皇帝、李亚子 | 本名 → 爵位 → 庙号 → 身份 → 小名 |
| **李克用** | 李克用、晋王、河东节度使、独眼龙、朱邪克用 | 本名 → 爵位 → 官职 → 绰号 → 胡名 |
| **朱温** | 朱温、朱全忠、朱晃、后梁太祖 | 本名 → 赐名 → 即位改名 → 庙号 |

系统通过 **LLM 语义理解 + 名字集合匹配 + 增量上下文反馈** 三层机制协同解决：

#### 第一层：LLM 语义理解（抽取阶段）

LLM 在阅读每个文本块时，被要求**理解语义后判断人物身份**，输出结构化信息：

```json
{
  "original_name": "李存勖",
  "aliases": ["晋王", "后唐庄宗"],
  "role": "皇帝"
}
```

关键在于：LLM 不是靠"晋王"这个字符串来判断身份，而是结合上下文语义来确定"晋王"在当前语境下指的是谁。

#### 第二层：名字集合匹配（入库阶段）

`merge_person()` 的核心逻辑：

```
新人物的所有名字 = {original_name} ∪ {aliases}
已有人物的所有名字 = {original_name} ∪ {aliases}

若两个集合有交集 → 判定为同一人 → 合并节点（更新别名、补充信息）
若无交集 → 创建新节点
```

#### 第三层：增量上下文反馈（对齐阶段）

每处理 20 个 chunk，系统从 Neo4j 拉取已入库的全部人物名单注入 LLM 提示词：

```
【已入库人物名单】
- 李克用（又名：朱邪克用、晋王、独眼龙）
- 李存勖（又名：李亚子、后唐庄宗）
...
```

后续 chunk 的 LLM 可看到名单，更准确地将新出现的称谓映射到已有人物。

#### 防污染机制：通用称号黑名单

"太宗"、"晋王"、"皇帝"、"太子"等称号在不同时期指向不同人物。系统维护了一份 `AMBIGUOUS_TITLES` 黑名单（100+ 个词条），这些通用称号**不参与跨人物的合并匹配**，避免误合并。

### LLM 别名清洗

初始抽取后，部分人物的别名可能存在错误（子女名字混入、不同人物名字混入等）。系统提供 LLM 批量清洗脚本（`scripts/llm_batch_cleanup.py`），由 LLM 逐个审查每个人物的别名列表并纠正。

---

## 八、API 接口

所有接口均以 `http://localhost:8000` 为基础 URL。

### 页面接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 知识图谱可视化主页面 |

### 图谱查询接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/graph/stats` | 获取图谱统计信息 |
| GET | `/api/graph/person/{name}` | 按名字查询人物关系网络 |
| GET | `/api/graph/person/{person_uid}/detail` | 获取人物完整详情（信息+事件+关系） |
| GET | `/api/graph/event/{event_uid}` | 获取事件详情（信息+参与者+原文片段） |
| GET | `/api/graph/succession` | 获取皇位更替链 |
| GET | `/api/graph/search?q=xxx` | 全文搜索人物 |

### 问答接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 知识库智能问答 |

请求体：
```json
{
  "question": "李克用有哪些义子？"
}
```

响应体：
```json
{
  "question": "李克用有哪些义子？",
  "cypher": "MATCH (p:Person)-[:ADOPTED_SON]->(s:Person) WHERE ...",
  "cypher_error": null,
  "graph_data": [...],
  "answer": "李克用收养了十三位义子，被称为"十三太保"..."
}
```

### 管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/run_ingestion` | 触发数据摄入 |

---

## 九、CLI 命令行工具

```bash
python cli.py <command> [options]
```

| 命令 | 参数 | 说明 |
|------|------|------|
| `seed` | `--clear` | 加载种子数据到 Neo4j（--clear 先清库） |
| `ingest` | `--clear`, `--no-resume`, `--max-chunks N`, `--start-from N` | 全量知识摄入（LLM 抽取 + 实时写入） |
| `ingest-saved` | — | 从已保存的抽取结果恢复写入（跳过 LLM） |
| `ask` | `"问题"` | 命令行智能问答 |
| `stats` | — | 查看图谱节点和关系统计 |
| `search` | `"人名"` | 按名字/别名搜索人物 |
| `serve` | — | 启动 Web 服务（等同 `python main.py`） |
| `process` | — | 仅做文本预处理（不摄入） |

示例：

```bash
# 测试摄入前 10 块
python cli.py ingest --max-chunks 10

# 从第 500 块开始续传
python cli.py ingest --start-from 500

# 命令行问答
python cli.py ask "后唐是怎么灭亡的？"

# 搜索人物
python cli.py search "元行钦"
```

---

## 十、项目目录结构

```
LiCunXu/
├── main.py                 # 应用入口（Uvicorn 启动 FastAPI 服务）
├── cli.py                  # 命令行工具（seed/ingest/ask/stats/serve/search/process）
├── pyproject.toml          # 项目依赖与元数据（Python 3.11+）
├── .env                    # 环境变量配置（Neo4j 连接、API Key 等）
│
├── config/                 # 配置管理
│   ├── settings.py         # 全局配置（Pydantic-Settings，支持 .env 加载）
│   └── llm_client.py       # LLM 客户端（VenusLLM + DeepSeekLLM 双引擎）
│
├── models/                 # 数据模型
│   └── entities.py         # Pydantic 实体定义（Person/Dynasty/Event/Place/Relation）
│
├── books/                  # 原始书本文本
│   ├── five_kindom.txt     # 《五代十国全史》(6.41 MB)
│   └── splited_empire.txt  # 《帝国的崩裂》(711 KB)
│
├── data/
│   ├── processed/          # 中间产物
│   │   ├── chunks.json     #   文本块（1790块, 8.3 MB）
│   │   ├── extraction_results.json  #  全量抽取结果 (21.4 MB)
│   │   ├── extraction_results_checkpoint_*.json  # 增量检查点
│   │   └── progress.json   #   断点续传进度
│   └── seed/
│       └── seed_data.py    # 种子数据（人工校验的朝代框架与核心人物）
│
├── ingestion/              # 数据摄入层
│   ├── text_processor.py   # 文本预处理（去目录 → 章节识别 → 句子级分块）
│   ├── extractor.py        # LLM 知识抽取器（改名感知提示词 + JSON 解析）
│   └── pipeline.py         # 端到端摄入流水线（逐块 → 实时写入 → 断点续传）
│
├── graph/                  # 图谱服务层
│   ├── connection.py       # Neo4j 连接管理（单例 + 上下文管理器）
│   ├── schema.py           # Schema 初始化（唯一约束 + 全文索引）
│   ├── crud.py             # 图谱 CRUD（merge_person 智能合并 + 通用称号黑名单）
│   └── seed_loader.py      # 种子数据加载器
│
├── rag/                    # RAG 问答层
│   └── engine.py           # Graph RAG 引擎（预定义查询 + Cypher 生成 + DeepSeek 回答）
│
├── api/                    # Web API 层
│   ├── routes.py           # FastAPI 路由（图谱查询 + 人物/事件详情 + 问答 + 管理）
│   └── visualization.py    # 前端 HTML 文件读取
│
├── static/                 # 前端静态资源
│   └── index.html          # 单页应用（vis-network 图谱可视化 + 问答面板 + 详情面板）
│
├── scripts/                # 工具脚本
│   └── llm_batch_cleanup.py  # LLM 批量别名清洗脚本
│
└── logs/                   # 运行日志
    ├── app.log             # 应用服务日志
    └── pipeline.log        # 摄入流水线日志
```

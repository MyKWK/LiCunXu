"""文本预处理 - 将原始书本内容切分为结构化章节块

针对两本五代十国书籍的实际格式做了精准适配：
1. five_kindom.txt：前面是目录，正文从"前言"正文开始，章节标题为 "| 第X章 |" 或单独一行 "第X章"
2. splited_empire.txt：前面是目录+自序，正文从"第一章"开始
"""

import json
import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from config.settings import settings


class TextChunk(BaseModel):
    """文本块"""
    chunk_id: str = Field(description="块 ID")
    chapter: str = Field(default="", description="所属章节")
    content: str = Field(description="文本内容")
    char_count: int = Field(default=0, description="字符数")


def read_raw_text(file_path: Path) -> str:
    """读取原始文本文件"""
    encodings = ["utf-8", "gbk", "gb2312", "utf-16"]
    for enc in encodings:
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法解码文件: {file_path}")


def _strip_toc(text: str) -> str:
    """智能去除文本开头的目录部分

    策略：
    1. 目录通常在正文之前，由大量短行（只有标题）组成
    2. 正文的段落通常较长（>100字符一段）
    3. 我们找到第一个"实质性长段落"的位置，取其前面最近的章节标题作为起点
    """
    lines = text.split('\n')
    
    # 找到第一个超过 100 字符的非空行（排除标题行），这大概率是正文开始
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # 跳过空行和明显的标题/短行
        if len(stripped) > 100 and not stripped.startswith('|') and not re.match(r'^第[一二三四五六七八九十百零\d]+[章回节篇]', stripped):
            body_start = i
            break

    if body_start == 0:
        return text

    # 从 body_start 往前找最近的章节标题或"前言"作为起点
    actual_start = body_start
    for i in range(body_start - 1, max(0, body_start - 30), -1):
        stripped = lines[i].strip()
        if stripped in ('前言', '自序', '序言', '序'):
            actual_start = i
            break
        if re.match(r'^\|?\s*第[一二三四五六七八九十百零\d]+[章回节篇]', stripped):
            actual_start = i
            break
        # 卷标题如 "五代十国全史①..."
        if re.match(r'^五代十国全史', stripped):
            actual_start = i
            break
        # "帝国的崩裂" 开头
        if re.match(r'^帝国的崩裂', stripped):
            actual_start = i
            break

    logger.info(f"  目录截止于第 {actual_start} 行（共 {len(lines)} 行），正文从此开始")
    return '\n'.join(lines[actual_start:])


def split_into_chapters(text: str, book_name: str = "") -> list[tuple[str, str]]:
    """将全文按章节标题切分

    支持的章节格式（按优先级）：
    - "| 第X章 |" (five_kindom.txt 正文格式)
    - "第X章 标题" 或 "第X章" (通用格式)
    - 卷标题如 "五代十国全史②..."

    Returns:
        list of (chapter_title, chapter_content) tuples
    """
    # 先去掉目录
    text = _strip_toc(text)

    # 合并多种章节分隔模式
    # Pattern 1: | 第X章 | 后面跟着一个子标题行
    # Pattern 2: 第X章 标题
    # Pattern 3: "五代十国全史②..."（卷分隔）
    # Pattern 4: "帝国的崩裂：..." （卷分隔）
    
    # 使用统一的正则来 split
    chapter_pattern = re.compile(
        r'('
        r'\|\s*第[一二三四五六七八九十百零\d]+[章回节篇]\s*\|'  # | 第X章 |
        r'|'
        r'^第[一二三四五六七八九十百零\d]+[章回节篇][^\n]*'  # 第X章 标题
        r'|'
        r'^五代十国全史[①②③④⑤⑥⑦⑧⑨⑩][^\n]*'  # 卷分隔
        r'|'
        r'^帝国的崩裂[：:][^\n]*'  # 另一本的卷分隔
        r')',
        re.MULTILINE
    )

    splits = chapter_pattern.split(text)

    if len(splits) <= 1:
        logger.warning("未检测到章节结构，将作为单一文本处理")
        return [("全文", text)]

    chapters = []
    current_title = "前言"
    subtitle_lines = []

    # splits 格式: [前导文本, 匹配1, 文本1, 匹配2, 文本2, ...]
    if splits[0].strip():
        # 前导文本（前言）
        content = _clean_content(splits[0])
        if len(content) > 50:
            chapters.append(("前言", content))

    for i in range(1, len(splits), 2):
        title = splits[i].strip()
        content = splits[i + 1].strip() if i + 1 < len(splits) else ""

        # 清理 "| 第X章 |" 格式，提取后面的子标题
        if title.startswith('|'):
            # 形如 "| 第一章 |"，后面的 content 开头几行可能是子标题
            clean_title = re.sub(r'[||\s]', '', title)  # "第一章"
            # 从 content 开头提取子标题（短行）
            content_lines = content.split('\n')
            sub_title = ""
            content_start = 0
            for j, line in enumerate(content_lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if len(stripped) < 30 and not re.search(r'[。！？；]', stripped):
                    # 短行，可能是子标题
                    sub_title = stripped
                    content_start = j + 1
                    break
                else:
                    break

            if sub_title:
                title = f"{clean_title} {sub_title}"
            else:
                title = clean_title
            content = '\n'.join(content_lines[content_start:])

        # 卷标题（如 "五代十国全史②万马逐鹿"），只做标记不作为独立章节
        if re.match(r'^五代十国全史|^帝国的崩裂', title):
            # 将卷标题信息附加到下一章的标题前缀
            current_book_marker = title
            if content.strip():
                # 如果卷标题后有内容，可能紧跟着前言
                clean_content = _clean_content(content)
                if len(clean_content) > 50:
                    chapters.append((title, clean_content))
            continue

        content = _clean_content(content)
        if len(content) > 50:  # 忽略太短的章节（可能是目录残余）
            chapters.append((title, content))

    logger.info(f"  识别到 {len(chapters)} 个章节")
    return chapters


def _clean_content(text: str) -> str:
    """清理章节内容"""
    # 去除多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去除行首行尾空白
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)
    return text.strip()


def chunk_text(
    text: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[str]:
    """将长文本按句子边界切分为重叠块

    Args:
        text: 输入文本
        chunk_size: 每块目标字符数
        overlap: 块间重叠字符数
    """
    # 按句子分割（中文句号、问号、感叹号、分号）
    sentences = re.split(r'([。！？；\n])', text)
    # 重新合并标点到句子末尾
    merged_sentences = []
    for i in range(0, len(sentences) - 1, 2):
        merged = sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else "")
        if merged.strip():
            merged_sentences.append(merged.strip())
    if len(sentences) % 2 == 1 and sentences[-1].strip():
        merged_sentences.append(sentences[-1].strip())

    chunks = []
    current_chunk = ""

    for sentence in merged_sentences:
        if len(current_chunk) + len(sentence) <= chunk_size:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            # 用重叠部分开始新块
            if overlap > 0 and current_chunk:
                overlap_text = current_chunk[-overlap:]
                current_chunk = overlap_text + sentence
            else:
                current_chunk = sentence

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def process_raw_files() -> list[TextChunk]:
    """处理 books/ 目录下的所有文本文件

    Returns:
        所有文本块列表
    """
    raw_dir = settings.RAW_DATA_DIR
    all_chunks: list[TextChunk] = []
    chunk_counter = 0

    # 支持的文件格式
    supported_exts = {".txt", ".md", ".text"}

    files = [f for f in raw_dir.iterdir() if f.suffix.lower() in supported_exts and f.is_file()]
    if not files:
        logger.warning(f"books/ 目录下未找到文本文件: {raw_dir}")
        return []

    for file_path in sorted(files):
        logger.info(f"处理文件: {file_path.name} ({file_path.stat().st_size / 1024:.0f} KB)")
        text = read_raw_text(file_path)
        chapters = split_into_chapters(text, book_name=file_path.stem)

        for chapter_title, chapter_content in chapters:
            chunks = chunk_text(chapter_content)
            for chunk_content in chunks:
                chunk_counter += 1
                tc = TextChunk(
                    chunk_id=f"chunk_{chunk_counter:05d}",
                    chapter=chapter_title,
                    content=chunk_content,
                    char_count=len(chunk_content),
                )
                all_chunks.append(tc)

    logger.info(f"共处理 {len(files)} 个文件，生成 {len(all_chunks)} 个文本块")

    # 保存处理结果
    output_path = settings.PROCESSED_DATA_DIR / "chunks.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([c.model_dump() for c in all_chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"文本块已保存至: {output_path}")

    return all_chunks


if __name__ == "__main__":
    chunks = process_raw_files()
    for c in chunks[:5]:
        print(f"[{c.chunk_id}] {c.chapter} ({c.char_count}字): {c.content[:100]}...")
    print(f"\n总计: {len(chunks)} 个文本块")
    # 统计各章节的块数
    chapter_counts = {}
    for c in chunks:
        chapter_counts[c.chapter] = chapter_counts.get(c.chapter, 0) + 1
    print("\n各章节块数:")
    for ch, count in list(chapter_counts.items())[:20]:
        print(f"  {ch}: {count} 块")

"""可视化前端 - 读取静态 HTML 文件"""

from pathlib import Path


def get_index_html() -> str:
    """返回知识图谱可视化与问答前端页面"""
    html_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")

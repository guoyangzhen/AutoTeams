"""分块服务（结构感知分块）。

重构说明（P1-1）：
之前 agent_builder._split_text 使用固定500字符+50重叠的简陋分块，
会在句子中间截断，破坏语义完整性。

现升级为结构感知分块：
1. 按自然段落/标题/Slide 等结构边界优先切分
2. 超长段落内部按句子切分
3. 保留上下文重叠
4. 支持 Contextual Retrieval：为每个块生成上下文摘要（可选）
"""
import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

# 分块参数
DEFAULT_CHUNK_SIZE = 800  # 目标块大小（字符数）
DEFAULT_CHUNK_OVERLAP = 100  # 块间重叠
MIN_CHUNK_SIZE = 50  # 最小块大小，低于此则合并到相邻块
MAX_CHUNK_SIZE = 2000  # 最大块大小，超过则强制切分


class ChunkerService:
    """结构感知分块服务。"""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(self, text: str, file_type: str = "document") -> list[str]:
        """将文本分块，感知文档结构。

        Args:
            text: 原始文本
            file_type: 文件类型（document/spreadsheet/presentation 等）

        Returns:
            分块后的文本列表
        """
        if not text or not text.strip():
            return []

        # P0-07: 限制最大输入长度，防止 OOM
        max_chars = settings.MAX_CHUNK_INPUT_CHARS
        if len(text) > max_chars:
            logger.warning(
                f"输入文本过长 ({len(text)} 字符)，截断至 {max_chars} 字符"
            )
            text = text[:max_chars]

        text = text.strip()

        # 根据文件类型选择分块策略
        if file_type == "presentation":
            chunks = self._chunk_by_slides(text)
        elif file_type == "spreadsheet":
            chunks = self._chunk_by_rows(text)
        else:
            chunks = self._chunk_by_structure(text)

        # 过滤空块和过短的块
        chunks = [c.strip() for c in chunks if c and c.strip()]
        chunks = [c for c in chunks if len(c) >= MIN_CHUNK_SIZE] or chunks

        return chunks

    def _chunk_by_structure(self, text: str) -> list[str]:
        """按文档结构（段落 → 句子）分块。"""
        # 1. 按双换行（段落）切分
        paragraphs = re.split(r"\n\s*\n", text)

        chunks: list[str] = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 如果当前段落本身超过目标块大小，按句子切分
            if len(para) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                chunks.extend(self._split_long_paragraph(para))
                continue

            # 如果加入当前段落后超长，先保存当前块
            if len(current_chunk) + len(para) + 2 > self.chunk_size and current_chunk:
                chunks.append(current_chunk)
                # 保留重叠：取当前块的末尾部分
                current_chunk = self._get_overlap(current_chunk) + "\n\n" + para
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _chunk_by_slides(self, text: str) -> list[str]:
        """按 Slide 切分（PPT 文档以 [Slide N] 为分隔符）。"""
        # 匹配 [Slide 1], [Slide 2] 等标记
        slide_pattern = r"\[Slide\s+\d+\]"
        slides = re.split(slide_pattern, text)

        chunks: list[str] = []
        slide_numbers = re.findall(slide_pattern, text)

        for i, slide_content in enumerate(slides):
            slide_content = slide_content.strip()
            if not slide_content:
                continue
            slide_label = slide_numbers[i - 1] if i > 0 and i - 1 < len(slide_numbers) else ""
            chunk = f"{slide_label}\n{slide_content}".strip()
            chunks.append(chunk)

        return chunks

    def _chunk_by_rows(self, text: str) -> list[str]:
        """按表格行分块（Excel 文档以 [Sheet] 为分隔符）。"""
        # 匹配 [Sheet] 标记
        sheet_pattern = r"\[.+\]"
        sheets = re.split(sheet_pattern, text)
        sheet_names = re.findall(sheet_pattern, text)

        chunks: list[str] = []
        for i, sheet_content in enumerate(sheets):
            sheet_content = sheet_content.strip()
            if not sheet_content:
                continue
            sheet_label = sheet_names[i - 1] if i > 0 and i - 1 < len(sheet_names) else ""
            chunk = f"{sheet_label}\n{sheet_content}".strip()
            # 如果表格外太大，按行切分
            if len(chunk) > MAX_CHUNK_SIZE:
                sub_chunks = self._split_long_paragraph(chunk)
                chunks.extend(sub_chunks)
            else:
                chunks.append(chunk)

        return chunks

    def _split_long_paragraph(self, para: str) -> list[str]:
        """对超长段落按句子切分。"""
        # 按中英文句号、问号、感叹号切分（在标点后分割，保留标点在句尾）
        sentences = re.split(r"(?<=[。！？.!?])", para)

        chunks: list[str] = []
        current = ""

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            if len(current) + len(sent) + 1 > self.chunk_size and current:
                chunks.append(current)
                current = self._get_overlap(current) + sent
            else:
                current = (current + sent).strip() if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _get_overlap(self, text: str) -> str:
        """获取块末尾的重叠部分。"""
        if len(text) <= self.chunk_overlap:
            return text
        # 尽量在句子边界处截取重叠
        overlap = text[-self.chunk_overlap:]
        # 找到第一个句号/换行后的位置
        boundary = re.search(r"[。！？.!?\n]", overlap)
        if boundary:
            return overlap[boundary.end():]
        return overlap


# 全局单例
chunker_service = ChunkerService()

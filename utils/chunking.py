import re
import unicodedata
def get_current_chapter(text):
    pattern = r"""(?imx)
        ^\s*                                   # đầu dòng, có thể có khoảng trắng
        (?:chương|chuong|chapter)              # hỗ trợ cả tiếng Việt và tiếng Anh
        [\s.:;\-–—_]*                          # các ký tự ngăn cách
        (?:[ivxlcdm]+|\d+(?:[\.,]\d+)?)?       # số La Mã hoặc số thường (nếu có)
        [\s.:;\-–—_]*                          # ngăn cách thêm
        [^\n]*                                 # tiêu đề (nếu có)
    """
    m = re.search(pattern, text)
    return m.group().strip() if m else None


def clean_text_keep_printable(s):
    cleaned = []
    for ch in s:
        if unicodedata.category(ch).startswith('C'):
            continue
        cleaned.append(ch)
    text = "".join(cleaned)
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    return text

def chunk_syllabus(text, chunk_size=5000):
    text = clean_text_keep_printable(text)

    chunks = []
    buffer = ""
    total_offset = 0
    current_chapter = None
    buffer_start_offset = 0

    idx = 0
    while idx < len(text):
        remaining = chunk_size - len(buffer)
        part = text[idx: idx + remaining]

        if not buffer:
            buffer_start_offset = total_offset

        buffer += part
        idx += len(part)
        total_offset += len(part)

        # Cập nhật chapter nếu gặp CHƯƠNG
        chap = get_current_chapter(part)
        if chap:
            current_chapter = chap

        if len(buffer) >= chunk_size:
            # Chỉ lấy current_chapter nếu có
            chapter_for_chunk = current_chapter

            chunks.append({
                "chapter": chapter_for_chunk,
                "content": buffer.strip(),
                "start_offset": buffer_start_offset,
                "end_offset": total_offset
            })
            buffer = ""

    # Xử lý buffer còn lại
    if buffer.strip():
        chapter_for_chunk = current_chapter
        chunks.append({
            "chapter": chapter_for_chunk,
            "content": buffer.strip(),
            "start_offset": buffer_start_offset,
            "end_offset": total_offset
        })

    return chunks

import unicodedata

def clean_text_keep_printable(s):
    cleaned = []
    for ch in s:
        # Loại bỏ ký tự "Control"
        if unicodedata.category(ch).startswith('C'):
            continue
        cleaned.append(ch)
    text = "".join(cleaned)

    # Thay các ký tự xuống dòng/tab bằng khoảng trắng
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    return text


def chunk_syllabus(text, chunk_size=5000):
    text = clean_text_keep_printable(text)

    chunks = []
    buffer = ""
    total_offset = 0
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

        if len(buffer) >= chunk_size:
            chunks.append({
                "content": buffer.strip(),
                "start_offset": buffer_start_offset,
                "end_offset": total_offset
            })
            buffer = ""

    # Xử lý phần còn lại
    if buffer.strip():
        chunks.append({
            "content": buffer.strip(),
            "start_offset": buffer_start_offset,
            "end_offset": total_offset
        })

    return chunks

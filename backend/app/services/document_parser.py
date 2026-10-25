import io
import re
from typing import List, Dict, Any, Tuple
import pypdf
import docx


class ParsedSection:
    def __init__(self, text: str, page_number: int = 1, metadata: Dict[str, Any] = None):
        self.text = text
        self.page_number = page_number
        self.metadata = metadata or {}


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    sections: List[ParsedSection] = []
    full_text_parts: List[str] = []

    for idx, page in enumerate(reader.pages):
        page_num = idx + 1
        page_text = page.extract_text() or ""
        page_text = clean_text(page_text)
        if page_text:
            sections.append(
                ParsedSection(
                    text=page_text,
                    page_number=page_num,
                    metadata={"page": page_num, "total_pages": len(reader.pages)},
                )
            )
            full_text_parts.append(f"--- Page {page_num} ---\n{page_text}")

    return "\n\n".join(full_text_parts), sections


def extract_text_from_docx(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    doc = docx.Document(io.BytesIO(file_bytes))
    sections: List[ParsedSection] = []
    full_text_parts: List[str] = []

    current_section_texts: List[str] = []
    current_heading = "General"
    page_approx = 1
    para_count = 0

    for para in doc.paragraphs:
        p_text = clean_text(para.text)
        if not p_text:
            continue

        if para.style and para.style.name.startswith("Heading"):
            if current_section_texts:
                sec_text = "\n".join(current_section_texts)
                sections.append(
                    ParsedSection(
                        text=sec_text,
                        page_number=page_approx,
                        metadata={"heading": current_heading, "approx_page": page_approx},
                    )
                )
                full_text_parts.append(sec_text)
                current_section_texts = []
            current_heading = p_text

        current_section_texts.append(p_text)
        para_count += 1
        if para_count % 8 == 0:
            page_approx += 1

    if current_section_texts:
        sec_text = "\n".join(current_section_texts)
        sections.append(
            ParsedSection(
                text=sec_text,
                page_number=page_approx,
                metadata={"heading": current_heading, "approx_page": page_approx},
            )
        )
        full_text_parts.append(sec_text)

    return "\n\n".join(full_text_parts), sections


def extract_text_from_txt(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")

    text = clean_text(text)
    section = ParsedSection(text=text, page_number=1, metadata={"format": "plain_text"})
    return text, [section]


def clean_text(text: str) -> str:
    if not text:
        return ""
    # Normalize unicode and whitespace
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_document(filename: str, file_bytes: bytes, mime_type: str = "") -> Tuple[str, List[ParsedSection]]:
    lower_name = filename.lower()
    if lower_name.endswith(".pdf") or "pdf" in mime_type:
        return extract_text_from_pdf(file_bytes)
    elif lower_name.endswith(".docx") or "wordprocessingml" in mime_type:
        return extract_text_from_docx(file_bytes)
    else:
        return extract_text_from_txt(file_bytes)


def chunk_document_sections(
    sections: List[ParsedSection],
    chunk_size: int = 600,
    chunk_overlap: int = 100,
) -> List[Dict[str, Any]]:
    """
    Chunks document sections into discrete chunks suitable for embedding and retrieval.
    Preserves page and paragraph metadata.
    """
    chunks: List[Dict[str, Any]] = []
    chunk_index = 0

    for section in sections:
        text = section.text
        if not text:
            continue

        words = text.split(" ")
        if len(words) <= chunk_size:
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": text,
                    "metadata": {
                        **section.metadata,
                        "page": section.page_number,
                        "word_count": len(words),
                        "char_length": len(text),
                    },
                }
            )
            chunk_index += 1
            continue

        # Split into overlapping windows of words
        step = max(1, chunk_size - chunk_overlap)
        for i in range(0, len(words), step):
            window = words[i : i + chunk_size]
            if not window:
                continue
            chunk_content = " ".join(window)
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": chunk_content,
                    "metadata": {
                        **section.metadata,
                        "page": section.page_number,
                        "word_count": len(window),
                        "char_length": len(chunk_content),
                        "start_word_idx": i,
                    },
                }
            )
            chunk_index += 1
            if i + chunk_size >= len(words):
                break

    return chunks

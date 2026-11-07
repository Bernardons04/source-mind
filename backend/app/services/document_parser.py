import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import docx
import pypdf
import tiktoken

# Shared token encoder (cl100k_base used by text-embedding-3-small and GPT-4o-mini)
_ENCODER = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Counts tokens using cl100k_base encoding."""
    if not text:
        return 0
    return len(_ENCODER.encode(text))


# ---------------------------------------------------------------------------
# Heading and Structural Detection Heuristics
# ---------------------------------------------------------------------------

# Numbered headings: "1.", "1.1", "2.3.1" followed by a capitalized/accented word
_NUMBERED_HEADING = re.compile(
    r"^(\d+)(\.\d+)*\.?\s+[A-ZÀ-Ú\u00c0-\u00ff][^:]{0,100}$"
)

# Standard Portuguese/English structural keywords
_KEYWORD_HEADING = re.compile(
    r"^(Seção|Secao|Capítulo|Capitulo|Artigo|Parte|Anexo|TÍTULO|TITULO|Cláusula|Clausula|Section|Chapter|Article|Appendix)\b",
    re.IGNORECASE,
)


def is_heading_text(line: str) -> bool:
    """Returns True if the stripped line represents a document section heading."""
    s = line.strip()
    if len(s) < 3 or len(s) > 120:
        return False
    if s.startswith("|"):
        return False
    # Reject lines ending in typical sentence punctuation unless strictly a numbered heading
    if s.endswith((".", ";", ":", ",")) and not re.match(r"^\d+(\.\d+)*\.?$", s):
        if not _NUMBERED_HEADING.match(s):
            return False
    return bool(_NUMBERED_HEADING.match(s)) or bool(_KEYWORD_HEADING.match(s))


def get_heading_level(line: str) -> int:
    """
    Computes hierarchy level:
    '1. Introdução' -> 1
    '1.1 Objetivo' -> 2
    '1.1.2 Escopo' -> 3
    Keyword-based headings default to level 1.
    """
    s = line.strip()
    m = re.match(r"^(\d+)((\.\d+)*)", s)
    if m:
        dots = m.group(2).count(".")
        return dots + 1
    return 1


# ---------------------------------------------------------------------------
# Text Normalization
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """
    Normalizes whitespace and line breaks without altering words or table structure.
    Preserves Markdown table pipes (|) on table lines.
    Collapses 3+ consecutive newlines to 2.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
            cleaned_lines.append(stripped)
        else:
            cleaned_lines.append(re.sub(r"[ \t]+", " ", stripped))
    joined = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


_BULLET_PATTERN = re.compile(r"^(?:[-*•●–—+]|\d+[.)])\s+")


def _should_preserve_line_break(prev_line: str, curr_line: str) -> bool:
    """
    Determines whether a line break between two consecutive lines within a paragraph block
    should be preserved.
    Preserves breaks for table rows, new list items, and introductory colon lines.
    Merges artificial line breaks caused by hard wrapping, sentence wrapping, or word-level extraction.
    """
    prev = prev_line.strip()
    curr = curr_line.strip()
    if not prev or not curr:
        return True
    if prev.startswith("|") or curr.startswith("|"):
        return True
    if _BULLET_PATTERN.match(curr):
        return True
    if _BULLET_PATTERN.match(prev):
        # If prev was a list item and curr is not a new list item, curr is a continuation line
        return False
    if prev.endswith(":"):
        return True
    return False


def _join_paragraph_lines(lines: List[str]) -> str:
    """
    Intelligently joins lines within a paragraph block.
    Removes artificial line breaks between words while preserving genuine structure
    (lists, colons, completed sentences, and hyphenated word wrapping).
    """
    cleaned = [l.strip() for l in lines if l.strip()]
    if not cleaned:
        return ""
    result = [cleaned[0]]
    for line in cleaned[1:]:
        prev = result[-1]
        if _should_preserve_line_break(prev, line):
            result.append(line)
        else:
            if prev.endswith("-") and not prev.endswith(" -") and len(prev) > 1 and prev[-2].isalpha():
                result[-1] = prev[:-1] + line
            else:
                result[-1] = prev + " " + line
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Structural Parsing Data Structures
# ---------------------------------------------------------------------------


class ParsedSection:
    """Represents an extracted raw section from a document with provenance."""

    def __init__(
        self,
        text: str,
        page_number: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.text = text
        self.page_number = page_number
        self.metadata = metadata or {}


@dataclass
class TextSegment:
    """Atomic text element associated with its page of origin and structural role."""

    text: str
    page: Optional[int] = None
    is_table_row: bool = False
    is_table_header: bool = False
    is_table_separator: bool = False
    is_heading: bool = False


@dataclass
class _SectionLine:
    text: str
    page: Optional[int] = None


@dataclass
class _DocSection:
    heading: str
    level: int
    lines: List[_SectionLine] = field(default_factory=list)
    heading_page: Optional[int] = None


# ---------------------------------------------------------------------------
# Splitter Helpers (Paragraph -> Sentence -> Word -> Token)
# ---------------------------------------------------------------------------

_ABBREVIATIONS = {
    "art", "arts", "sec", "secao", "seção", "cap", "capitulo", "capítulo",
    "ex", "exs", "fl", "fls", "sr", "sra", "dr", "dra", "prof", "profa",
    "obs", "pag", "pags", "pág", "págs", "vol", "vols", "num", "núm", "no", "nº",
    "inc", "par", "etc", "dept", "depto", "cia", "ltda", "tel", "av", "al", "ed",
}


def _split_sentences_pt(text: str) -> List[str]:
    """
    Splits text into sentences respecting abbreviations in Portuguese without requiring heavy NLP.
    Does not break on common abbreviations like 'art.', 'dr.', 'fl.', 'pág.', etc.
    """
    candidates = list(re.finditer(r"([.!?]+)(\s+)(?=[A-ZÀ-Ú\u00c0-\u00ff\d])", text))
    if not candidates:
        return [text] if text.strip() else []

    sentences: List[str] = []
    last_idx = 0
    for match in candidates:
        punct = match.group(1)
        end_punct_idx = match.start() + len(punct)
        start_next_idx = match.end()

        preceding = text[last_idx:match.start()].strip()
        last_word = preceding.split()[-1].lower() if preceding.split() else ""
        last_word = re.sub(r"^[^\w]+|[^\w]+$", "", last_word)

        if last_word in _ABBREVIATIONS:
            # Preceding word is a known abbreviation; ignore this split
            continue

        sentence = text[last_idx:end_punct_idx].strip()
        if sentence:
            sentences.append(sentence)
        last_idx = start_next_idx

    tail = text[last_idx:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _split_text_to_segments(text: str, page: Optional[int], target_tokens: int) -> List[TextSegment]:
    """
    Recursively splits oversized text into segments respecting token limits.
    Hierarchy:
    1. Paragraph split (\n\n)
    2. Portuguese abbreviation-aware sentence split
    3. Word boundary split
    4. Token/character slice (safe fallback)
    """
    if count_tokens(text) <= target_tokens:
        return [TextSegment(text=text, page=page)]

    # 1. Paragraph split
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) > 1:
        res: List[TextSegment] = []
        for p in paras:
            res.extend(_split_text_to_segments(p, page, target_tokens))
        return res

    # 2. Sentence split (abbreviation-aware)
    sentences = _split_sentences_pt(text)
    if len(sentences) > 1:
        res = []
        for s in sentences:
            res.extend(_split_text_to_segments(s, page, target_tokens))
        return res

    # 3. Word split
    words = text.split()
    if len(words) > 1:
        res = []
        curr_words: List[str] = []
        for w in words:
            w_tok = count_tokens(w)
            # If an individual word exceeds target_tokens, flush current words and slice the word
            if w_tok > target_tokens:
                if curr_words:
                    res.append(TextSegment(text=" ".join(curr_words), page=page))
                    curr_words = []
                w_slices = _split_text_to_segments(w, page, target_tokens)
                res.extend(w_slices)
                continue

            candidate = " ".join(curr_words + [w])
            if count_tokens(candidate) <= target_tokens:
                curr_words.append(w)
            else:
                if curr_words:
                    res.append(TextSegment(text=" ".join(curr_words), page=page))
                curr_words = [w]
        if curr_words:
            res.append(TextSegment(text=" ".join(curr_words), page=page))
        return res

    # 4. Token slicing fallback
    tokens = _ENCODER.encode(text)
    res = []
    start = 0
    while start < len(tokens):
        end = min(start + target_tokens, len(tokens))
        res.append(TextSegment(text=_ENCODER.decode(tokens[start:end]), page=page))
        if end >= len(tokens):
            break
        start = end
    return res


def _split_oversized_table_row(row_seg: TextSegment, target_tokens: int) -> List[TextSegment]:
    """
    Splits an oversized Markdown table row while preserving pipe table structure and ALL cell values.
    Guarantees:
    - Every slice of every cell is consumed exactly once.
    - No slice is accepted if the resulting row string exceeds target_tokens.
    - The algorithm always makes forward progress and terminates deterministically.
    - All original cells are preserved, including when 2+ cells in the same row are large.
    - Continuation rows maintain understandable association with the original row columns.
    - Continuation markers are fully counted in the token budget.
    """
    raw = row_seg.text.strip()
    if not (raw.startswith("|") and raw.endswith("|")):
        return _split_text_to_segments(row_seg.text, row_seg.page, target_tokens)

    cells = [c.strip() for c in raw.strip("|").split("|")]
    if not cells:
        return [row_seg]

    num_cols = len(cells)
    pipe_overhead = count_tokens("| " + " | ".join([""] * num_cols) + " |") + 2
    available_budget = max(5, target_tokens - pipe_overhead)

    # 1. Divide each cell into atomic slices that fit within available_budget
    cell_token_counts = [count_tokens(c) for c in cells]
    cell_slices: List[List[str]] = []
    for c, tok in zip(cells, cell_token_counts):
        if tok <= available_budget:
            cell_slices.append([c] if c else [""])
        else:
            sub_segs = _split_text_to_segments(c, row_seg.page, available_budget)
            cell_slices.append([s.text for s in sub_segs])

    # 2. Greedily pack cell slices into continuation rows
    result_rows: List[TextSegment] = []
    indices = [0] * num_cols

    def has_more() -> bool:
        return any(indices[i] < len(cell_slices[i]) for i in range(num_cols))

    while has_more():
        current_cells = [""] * num_cols
        progress_made = False

        for col_idx in range(num_cols):
            if indices[col_idx] < len(cell_slices[col_idx]):
                slice_val = cell_slices[col_idx][indices[col_idx]]
                cand_val = f"(cont.) {slice_val}" if indices[col_idx] > 0 else slice_val

                test_cells = list(current_cells)
                test_cells[col_idx] = cand_val
                test_row = "| " + " | ".join(test_cells) + " |"

                if count_tokens(test_row) <= target_tokens:
                    current_cells[col_idx] = cand_val
                    indices[col_idx] += 1
                    progress_made = True

        if not progress_made:
            # The next slice in the first pending column does not fit even alone in a row.
            # Slice that individual slice value directly to fit target_tokens - pipe_overhead.
            first_pending = next(i for i in range(num_cols) if indices[i] < len(cell_slices[i]))
            raw_slice = cell_slices[first_pending][indices[first_pending]]
            prefix = "(cont.) " if indices[first_pending] > 0 else ""
            prefix_tokens = count_tokens(prefix)
            cell_direct_budget = max(1, target_tokens - pipe_overhead - prefix_tokens - 1)

            broken_segs = _split_text_to_segments(raw_slice, row_seg.page, cell_direct_budget)
            # Replace the pending slice with its smaller fragments in cell_slices
            cell_slices[first_pending] = (
                cell_slices[first_pending][:indices[first_pending]]
                + [s.text for s in broken_segs]
                + cell_slices[first_pending][indices[first_pending] + 1:]
            )
            continue

        row_str = "| " + " | ".join(current_cells) + " |"
        result_rows.append(TextSegment(text=row_str, page=row_seg.page, is_table_row=True))

    return result_rows


def _split_oversized_table(
    table_segs: List[TextSegment],
    target_tokens: int,
) -> List[List[TextSegment]]:
    """
    Splits an oversized Markdown table across rows, repeating headers & separators on each slice
    whenever the header fits within the token budget.
    If the header alone exceeds or meets the budget, falls back to raw row grouping to ensure
    all chunks strictly respect target_tokens without failing or dropping content.
    Guarantees every candidate slice (header + rows) strictly respects target_tokens.
    """
    if not table_segs:
        return []

    header_segs = [s for s in table_segs if s.is_table_header or s.is_table_separator]
    data_rows = [s for s in table_segs if not (s.is_table_header or s.is_table_separator)]

    header_text = "\n".join(s.text for s in header_segs)
    header_tokens = count_tokens(header_text)

    # If header alone exceeds or takes almost all budget (> 80%), fallback to direct row chunking
    if header_tokens >= target_tokens or (target_tokens - header_tokens) < 15:
        slices: List[List[TextSegment]] = []
        curr: List[TextSegment] = []
        for s in table_segs:
            s_tok = count_tokens(s.text)
            if s_tok > target_tokens:
                if curr:
                    slices.append(curr)
                    curr = []
                # Split oversized individual row or header
                if s.is_table_row:
                    split_rows = _split_oversized_table_row(s, target_tokens)
                else:
                    split_rows = _split_text_to_segments(s.text, s.page, target_tokens)
                for sr in split_rows:
                    slices.append([sr])
                continue

            test_text = "\n".join(x.text for x in (curr + [s]))
            if curr and count_tokens(test_text) > target_tokens:
                slices.append(curr)
                curr = [s]
            else:
                curr.append(s)
        if curr:
            slices.append(curr)
        return slices

    # Branch where header fits in target_tokens
    slices: List[List[TextSegment]] = []
    curr_slice: List[TextSegment] = list(header_segs)

    for row_seg in data_rows:
        # Check if row_seg fits directly into curr_slice
        candidate = curr_slice + [row_seg]
        candidate_tokens = count_tokens("\n".join(s.text for s in candidate))

        if candidate_tokens <= target_tokens:
            curr_slice = candidate
        else:
            # If curr_slice has rows beyond headers, flush it first
            if len(curr_slice) > len(header_segs):
                slices.append(curr_slice)
                curr_slice = list(header_segs)

            # Test if row_seg fits alone with header
            candidate_alone = list(header_segs) + [row_seg]
            if count_tokens("\n".join(s.text for s in candidate_alone)) <= target_tokens:
                curr_slice = candidate_alone
            else:
                # row_seg exceeds remaining budget even alone with header: split the row
                header_plus_sep_tok = count_tokens("\n".join(s.text for s in header_segs) + "\n")
                row_avail_budget = max(5, target_tokens - header_plus_sep_tok)
                split_rows = _split_oversized_table_row(row_seg, row_avail_budget)

                for sr in split_rows:
                    sr_candidate = list(header_segs) + [sr]
                    sr_tok = count_tokens("\n".join(s.text for s in sr_candidate))
                    if sr_tok <= target_tokens:
                        slices.append(sr_candidate)
                    else:
                        # If repeating header still exceeds, emit sr by itself to strictly respect target_tokens
                        slices.append([sr])

    if len(curr_slice) > len(header_segs):
        slices.append(curr_slice)

    return slices


def _take_overlap_segments(
    segments: List[TextSegment],
    token_budget: int,
) -> List[TextSegment]:
    """Takes trailing non-table segments up to token_budget, preserving page provenance."""
    if token_budget <= 0 or not segments:
        return []

    chosen: List[TextSegment] = []
    accum_tokens = 0

    for seg in reversed(segments):
        if seg.is_table_header or seg.is_table_separator or seg.is_table_row:
            continue
        seg_tok = count_tokens(seg.text)
        sep_tok = count_tokens("\n\n") if chosen else 0

        if accum_tokens + sep_tok + seg_tok <= token_budget:
            chosen.insert(0, seg)
            accum_tokens += sep_tok + seg_tok
        else:
            remain = token_budget - accum_tokens - sep_tok
            if remain >= 10:
                toks = _ENCODER.encode(seg.text)
                tail = _ENCODER.decode(toks[-remain:]).strip()
                if tail:
                    chosen.insert(0, TextSegment(text=tail, page=seg.page))
            break

    return chosen


def _merge_segments_to_chunks(
    segments: List[TextSegment],
    target_tokens: int,
    overlap_tokens: int,
    current_parent_heading: Optional[str] = None,
    sub_heading: Optional[str] = None,
) -> List[Tuple[str, List[Optional[int]], List[TextSegment]]]:
    """Greedily merges atomic TextSegments into chunk tuples of (content_text, page_list, segment_list)."""
    chunks_out: List[Tuple[str, List[Optional[int]], List[TextSegment]]] = []

    # 1. Group into atomic blocks (tables or paragraphs)
    atomic_units: List[List[TextSegment]] = []
    idx = 0
    while idx < len(segments):
        seg = segments[idx]
        if seg.is_table_header or seg.is_table_separator or seg.is_table_row:
            table_block = []
            while idx < len(segments) and (
                segments[idx].is_table_header
                or segments[idx].is_table_separator
                or segments[idx].is_table_row
            ):
                table_block.append(segments[idx])
                idx += 1
            tbl_text = "\n".join(s.text for s in table_block)
            if count_tokens(tbl_text) > target_tokens:
                slices = _split_oversized_table(table_block, target_tokens)
                for sl in slices:
                    atomic_units.append(sl)
            else:
                atomic_units.append(table_block)
        else:
            if count_tokens(seg.text) > target_tokens:
                split_segs = _split_text_to_segments(seg.text, seg.page, target_tokens)
                for s in split_segs:
                    atomic_units.append([s])
            else:
                atomic_units.append([seg])
            idx += 1

    # 2. Greedily merge atomic blocks
    curr_segs: List[TextSegment] = []
    curr_text = ""
    curr_tokens = 0

    def emit_current_chunk():
        nonlocal curr_segs, curr_text, curr_tokens
        if curr_segs and curr_text.strip():
            chunk_pages = [
                s.page for s in curr_segs
                if not (s.is_heading and s.text == current_parent_heading and sub_heading)
            ]
            if not chunk_pages:
                chunk_pages = [s.page for s in curr_segs]
            chunks_out.append((curr_text, chunk_pages, list(curr_segs)))
            curr_segs = []
            curr_text = ""
            curr_tokens = 0

    def emit_oversized_unit(u: List[TextSegment]):
        is_tbl = any(s.is_table_row or s.is_table_header or s.is_table_separator for s in u)
        if is_tbl:
            sub_tbl_slices = _split_oversized_table(u, target_tokens)
            for sts in sub_tbl_slices:
                sts_text = "\n".join(s.text for s in sts)
                sts_pages = [s.page for s in sts]
                chunks_out.append((sts_text, sts_pages, list(sts)))
        else:
            for s in u:
                sub_txt_segs = _split_text_to_segments(s.text, s.page, target_tokens)
                for sts in sub_txt_segs:
                    chunks_out.append((sts.text, [sts.page], [sts]))

    for unit in atomic_units:
        unit_text = "\n".join(s.text for s in unit)
        unit_tokens = count_tokens(unit_text)
        sep_tokens = count_tokens("\n\n") if curr_text else 0

        if curr_tokens + sep_tokens + unit_tokens <= target_tokens:
            curr_segs.extend(unit)
            curr_text = (curr_text + "\n\n" + unit_text).strip() if curr_text else unit_text
            curr_tokens = count_tokens(curr_text)
        else:
            if curr_segs:
                # Save reference to previous chunk's segments before flushing for overlap calculation
                prev_segs = list(curr_segs)
                emit_current_chunk()

                # If the incoming unit itself exceeds target_tokens, divide it directly!
                if unit_tokens > target_tokens:
                    emit_oversized_unit(unit)
                    continue

                # Incoming unit fits in target_tokens: apply overlap if not a table
                is_unit_table = any(s.is_table_row or s.is_table_header or s.is_table_separator for s in unit)
                if not is_unit_table and overlap_tokens > 0:
                    budget = max(0, min(overlap_tokens, target_tokens - unit_tokens - count_tokens("\n\n")))
                    overlap_segs = _take_overlap_segments(prev_segs, budget)
                    if overlap_segs:
                        candidate_segs = list(overlap_segs) + list(unit)
                        candidate_text = "\n\n".join(s.text for s in candidate_segs).strip()
                        candidate_tokens = count_tokens(candidate_text)
                        while candidate_tokens > target_tokens and overlap_segs:
                            overlap_segs.pop(0)
                            if overlap_segs:
                                candidate_segs = list(overlap_segs) + list(unit)
                                candidate_text = "\n\n".join(s.text for s in candidate_segs).strip()
                                candidate_tokens = count_tokens(candidate_text)
                            else:
                                candidate_segs = list(unit)
                                candidate_text = unit_text
                                candidate_tokens = unit_tokens
                        curr_segs = candidate_segs
                        curr_text = candidate_text
                        curr_tokens = candidate_tokens
                    else:
                        curr_segs = list(unit)
                        curr_text = unit_text
                        curr_tokens = unit_tokens
                else:
                    curr_segs = list(unit)
                    curr_text = unit_text
                    curr_tokens = unit_tokens
            else:
                # curr_segs was empty: unit alone exceeds target_tokens!
                emit_oversized_unit(unit)

    emit_current_chunk()

    return chunks_out


# ---------------------------------------------------------------------------
# Format Extractors (PDF, DOCX, TXT)
# ---------------------------------------------------------------------------


def _is_word_stacked(text: str) -> bool:
    """
    Detects if extracted PDF text suffers from artificial word stacking
    (e.g. one word per line caused by character/word-level PDF text positioning).
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return False
    single_word_lines = sum(1 for l in lines if " " not in l)
    return (single_word_lines / len(lines)) > 0.6


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    """
    Extracts text from PDF page by page.
    Preserves exact 1-indexed page provenance for every section.
    Uses layout-aware extraction to reconstruct natural lines and paragraphs,
    with automatic fallback to plain extraction when layout mode fails or is empty.
    """
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    sections: List[ParsedSection] = []
    full_text_parts: List[str] = []
    total_pages = len(reader.pages)

    for idx, page in enumerate(reader.pages):
        page_num = idx + 1
        page_text = ""

        # 1. Attempt layout mode extraction first (fixes word-level vertical displacement)
        try:
            layout_candidate = page.extract_text(extraction_mode="layout") or ""
            if layout_candidate.strip() and not _is_word_stacked(layout_candidate):
                page_text = layout_candidate
        except Exception:
            page_text = ""

        # 2. Fallback to standard plain extraction if layout is unavailable or stacked
        if not page_text.strip():
            try:
                plain_candidate = page.extract_text() or ""
                page_text = plain_candidate
            except Exception:
                page_text = ""

        page_text = clean_text(page_text)
        if page_text:
            sections.append(
                ParsedSection(
                    text=page_text,
                    page_number=page_num,
                    metadata={"page": page_num, "total_pages": total_pages},
                )
            )
            full_text_parts.append(f"--- Page {page_num} ---\n{page_text}")

    return "\n\n".join(full_text_parts), sections


def _table_to_markdown(table: docx.table.Table) -> str:
    """Converts a docx Table object into a clean Markdown table with headers and separator."""
    if not table.rows:
        return ""

    rows_data: List[List[str]] = []
    for row in table.rows:
        row_cells = [clean_text(cell.text).replace("\n", " ").replace("|", "/") for cell in row.cells]
        rows_data.append(row_cells)

    if not rows_data or not rows_data[0]:
        return ""

    num_cols = len(rows_data[0])
    lines: List[str] = []

    # Header line
    header_line = "| " + " | ".join(rows_data[0]) + " |"
    sep_line = "| " + " | ".join(["---"] * num_cols) + " |"
    lines.append(header_line)
    lines.append(sep_line)

    for row_cells in rows_data[1:]:
        if len(row_cells) < num_cols:
            row_cells = row_cells + [""] * (num_cols - len(row_cells))
        elif len(row_cells) > num_cols:
            row_cells = row_cells[:num_cols]
        lines.append("| " + " | ".join(row_cells) + " |")

    return "\n".join(lines)


def extract_text_from_docx(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    """
    Extracts text and tables from DOCX, respecting document flow and native styles.
    Preserves Word Heading styles explicitly without creating artificial page numbers.
    """
    doc = docx.Document(io.BytesIO(file_bytes))
    sections: List[ParsedSection] = []
    full_text_parts: List[str] = []

    current_section_texts: List[str] = []
    current_heading: Optional[str] = None
    current_heading_level = 1

    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[-1]

        if tag == "p":
            p = docx.text.paragraph.Paragraph(child, doc)
            p_text = clean_text(p.text)
            if not p_text:
                continue

            style_name = p.style.name if p.style else ""
            # Prioritize native Word Heading styles explicitly
            is_native_heading = style_name.startswith("Heading") or style_name.startswith("Título") or style_name.startswith("Titulo")
            is_heuristic_heading = is_heading_text(p_text)

            if is_native_heading or is_heuristic_heading:
                if current_section_texts:
                    sec_text = "\n\n".join(current_section_texts)
                    sections.append(
                        ParsedSection(
                            text=sec_text,
                            page_number=None,  # No artificial pagination for DOCX
                            metadata={
                                "heading": current_heading,
                                "heading_level": current_heading_level,
                                "is_explicit_heading": True,
                            },
                        )
                    )
                    full_text_parts.append(sec_text)
                    current_section_texts = []

                current_heading = p_text
                if "1" in style_name:
                    current_heading_level = 1
                elif "2" in style_name:
                    current_heading_level = 2
                elif "3" in style_name:
                    current_heading_level = 3
                else:
                    current_heading_level = get_heading_level(p_text)

            current_section_texts.append(p_text)

        elif tag == "tbl":
            table = docx.table.Table(child, doc)
            tbl_md = _table_to_markdown(table)
            if tbl_md:
                current_section_texts.append(tbl_md)

    if current_section_texts:
        sec_text = "\n\n".join(current_section_texts)
        sections.append(
            ParsedSection(
                text=sec_text,
                page_number=None,
                metadata={
                    "heading": current_heading,
                    "heading_level": current_heading_level,
                    "is_explicit_heading": True,
                },
            )
        )
        full_text_parts.append(sec_text)

    return "\n\n".join(full_text_parts), sections


def extract_text_from_txt(file_bytes: bytes) -> Tuple[str, List[ParsedSection]]:
    """Extracts text from TXT with encoding fallback and no artificial pagination."""
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")

    text = clean_text(text)
    section = ParsedSection(
        text=text,
        page_number=None,
        metadata={"format": "plain_text", "page": None, "total_pages": None},
    )
    return text, [section]


def parse_document(filename: str, file_bytes: bytes, mime_type: str = "") -> Tuple[str, List[ParsedSection]]:
    """Routes document bytes to the appropriate extractor based on extension and mime_type."""
    lower_name = filename.lower()
    if lower_name.endswith(".pdf") or "pdf" in mime_type:
        return extract_text_from_pdf(file_bytes)
    elif lower_name.endswith(".docx") or "wordprocessingml" in mime_type:
        return extract_text_from_docx(file_bytes)
    else:
        return extract_text_from_txt(file_bytes)


# ---------------------------------------------------------------------------
# Section-Aware Structural Chunking
# ---------------------------------------------------------------------------


def _build_doc_sections(sections: List[ParsedSection]) -> List[_DocSection]:
    """
    Converts list of ParsedSection into structured _DocSection list.
    Preserves explicit headings from structured parsers (e.g. DOCX) without re-evaluating regex,
    and groups paragraphs respecting line-level page provenance for PDF.
    """
    doc_sections: List[_DocSection] = []

    for sec in sections:
        page_num = sec.page_number
        is_explicit = sec.metadata.get("is_explicit_heading", False)
        explicit_heading = sec.metadata.get("heading")
        explicit_level = sec.metadata.get("heading_level") or 1

        if is_explicit or explicit_heading:
            # Heading was explicitly provided in metadata (e.g. DOCX or pre-parsed section)
            # Lines belong to this section without needing regex re-identification
            lines = sec.text.split("\n")
            sec_lines: List[_SectionLine] = []
            for line in lines:
                # If the line is the heading itself, don't duplicate it in lines
                if explicit_heading and line.strip() == explicit_heading.strip():
                    continue
                sec_lines.append(_SectionLine(text=line, page=page_num))

            doc_sections.append(
                _DocSection(
                    heading=explicit_heading or "",
                    level=explicit_level if explicit_heading else 0,
                    lines=sec_lines,
                    heading_page=page_num,
                )
            )
        else:
            # Free-form text (PDF, TXT): split into paragraphs/lines and detect headings
            current: Optional[_DocSection] = None
            lines = sec.text.split("\n")

            for line in lines:
                raw = line
                stripped = line.strip()

                if is_heading_text(stripped):
                    if current is not None and (current.lines or current.heading):
                        doc_sections.append(current)
                    current = _DocSection(
                        heading=stripped,
                        level=get_heading_level(stripped),
                        lines=[],
                        heading_page=page_num,
                    )
                else:
                    if current is None:
                        current = _DocSection(heading="", level=0, lines=[], heading_page=page_num)
                    current.lines.append(_SectionLine(text=raw, page=page_num))

            if current is not None and (current.lines or current.heading):
                doc_sections.append(current)

    return doc_sections


def chunk_document_sections(
    sections: List[ParsedSection],
    chunk_size: int = 500,          # in tokens (tiktoken cl100k_base)
    chunk_overlap: int = 80,        # in tokens
    min_chunk_size: int = 20,       # in tokens
    document_name: str = "",
) -> List[Dict[str, Any]]:
    """
    Structure-aware chunking strategy for ManualMind:
    - Token counting using tiktoken (cl100k_base).
    - Preserves heading hierarchy (Level 1 sections injected into sub-chunks for retrieval context).
    - Preserves exact page and page_end provenance for PDF (None for DOCX/TXT).
    - Slices oversized Markdown tables across rows while repeating column headers and separators.
    - Preserves context of sibling cells in continuation rows of oversized table rows.
    - Divides plain text by paragraphs, then abbreviation-aware Portuguese sentences, then words, then tokens.
    - Strictly respects chunk_size token limit without converting tables to plain text.
    - Outputs Dict[str, Any] with chunk_index, content, and metadata matching database schema.
    """
    if not sections:
        return []

    # Calculate real total pages only if valid (PDF). None for unpaginated formats (DOCX/TXT).
    valid_pages = [sec.metadata.get("total_pages") for sec in sections if sec.metadata.get("total_pages") is not None]
    total_pages: Optional[int] = max(valid_pages) if valid_pages else None

    doc_sections = _build_doc_sections(sections)
    chunks_out: List[Dict[str, Any]] = []
    current_parent_heading: Optional[str] = None
    current_parent_page: Optional[int] = None
    global_index = 0

    for i, sec in enumerate(doc_sections):
        lvl = sec.level
        hd = sec.heading
        body_lines = [l.text for l in sec.lines]
        body = "\n".join(body_lines).strip()

        if lvl == 1:
            current_parent_heading = hd
            current_parent_page = sec.heading_page
            sub_heading: Optional[str] = None
        elif lvl >= 2:
            sub_heading = hd
        else:
            current_parent_heading = None
            current_parent_page = None
            sub_heading = None

        # Standalone parent heading that introduces subsections
        if lvl == 1 and not body:
            has_following_sub = (
                i + 1 < len(doc_sections) and doc_sections[i + 1].level >= 2
            )
            if has_following_sub:
                continue

        section_segments: List[TextSegment] = []

        # 1. Inherited parent heading for context
        if current_parent_heading and sub_heading:
            section_segments.append(
                TextSegment(
                    text=current_parent_heading,
                    page=current_parent_page or sec.heading_page,
                    is_heading=True,
                )
            )

        # 2. Section / Subsection heading
        if hd:
            section_segments.append(
                TextSegment(
                    text=hd,
                    page=sec.heading_page,
                    is_heading=True,
                )
            )

        # 3. Body lines & table identification
        # Group non-table consecutive lines into whole paragraphs instead of raw single lines
        in_table = False
        table_header_seen = False
        curr_para_lines: List[str] = []
        curr_para_page: Optional[int] = None

        for line_obj in sec.lines:
            raw_text = line_obj.text.strip()
            if not raw_text:
                if curr_para_lines:
                    section_segments.append(
                        TextSegment(text=_join_paragraph_lines(curr_para_lines), page=curr_para_page)
                    )
                    curr_para_lines = []
                    curr_para_page = None
                continue

            is_tbl = raw_text.startswith("|")
            if is_tbl:
                if curr_para_lines:
                    section_segments.append(
                        TextSegment(text=_join_paragraph_lines(curr_para_lines), page=curr_para_page)
                    )
                    curr_para_lines = []
                    curr_para_page = None

                is_sep = bool(re.match(r"^\|\s*---.*\|$", raw_text))
                if not in_table:
                    in_table = True
                    table_header_seen = False
                    is_hdr = True
                elif is_sep:
                    table_header_seen = True
                    is_hdr = False
                elif not table_header_seen:
                    is_hdr = True
                else:
                    is_hdr = False

                section_segments.append(
                    TextSegment(
                        text=raw_text,
                        page=line_obj.page,
                        is_table_row=not (is_hdr or is_sep),
                        is_table_header=is_hdr,
                        is_table_separator=is_sep,
                    )
                )
            else:
                in_table = False
                table_header_seen = False
                if curr_para_page is None:
                    curr_para_page = line_obj.page
                curr_para_lines.append(raw_text)

        if curr_para_lines:
            section_segments.append(
                TextSegment(text=_join_paragraph_lines(curr_para_lines), page=curr_para_page)
            )

        if not section_segments:
            continue

        full_content_test = clean_text("\n".join(s.text for s in section_segments))
        tok = count_tokens(full_content_test)
        if tok < min_chunk_size and not body:
            continue

        chunk_pieces = _merge_segments_to_chunks(
            section_segments,
            target_tokens=chunk_size,
            overlap_tokens=chunk_overlap,
            current_parent_heading=current_parent_heading,
            sub_heading=sub_heading,
        )

        for content_text, content_pages, chunk_segs in chunk_pieces:
            cleaned_content = clean_text(content_text)
            if not cleaned_content:
                continue

            piece_tok = count_tokens(cleaned_content)
            if piece_tok > chunk_size:
                raise ValueError(
                    f"Chunk exceeds chunk_size limit ({piece_tok} > {chunk_size} tokens). "
                    "Integrity check failed: chunks must strictly respect the token limit."
                )

            re_chunk_pieces = [(cleaned_content, content_pages)]

            for sub_content, sub_pages in re_chunk_pieces:
                sub_tok = count_tokens(sub_content)
                if sub_tok < min_chunk_size and chunks_out:
                    continue

                valid_sub_pages = [p for p in sub_pages if p is not None]
                if valid_sub_pages:
                    p_start: Optional[int] = min(valid_sub_pages)
                    p_end: Optional[int] = max(valid_sub_pages)
                else:
                    p_start = None
                    p_end = None

                meta: Dict[str, Any] = {
                    "page": p_start,
                    "page_end": p_end,
                    "total_pages": total_pages,
                    "chunk_index": global_index,
                    "token_count": sub_tok,
                    "char_length": len(sub_content),
                    "section": current_parent_heading,
                    "subsection": sub_heading,
                    "heading": sub_heading or current_parent_heading,
                    "document_name": document_name,
                }

                chunks_out.append(
                    {
                        "chunk_index": global_index,
                        "content": sub_content,
                        "metadata": meta,
                    }
                )
                global_index += 1

    return chunks_out

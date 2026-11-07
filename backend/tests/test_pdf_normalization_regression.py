import pytest
from app.services.document_parser import (
    extract_text_from_pdf,
    chunk_document_sections,
    _is_word_stacked,
    _join_paragraph_lines,
    _should_preserve_line_break,
    _BULLET_PATTERN,
)


def test_is_word_stacked_detector():
    stacked_sample = "Northstar\n \nSolutions\n \nRemote\n \nWork\n \nGuidelines\n \nEffective\n \nDate:"
    assert _is_word_stacked(stacked_sample) is True

    normal_sample = "Northstar Solutions\nRemote Work Guidelines\nEffective Date: January 1, 2026"
    assert _is_word_stacked(normal_sample) is False


def test_bullet_pattern_supports_black_circle_and_standard_bullets():
    assert _BULLET_PATTERN.match("● Use the company's approved VPN") is not None
    assert _BULLET_PATTERN.match("• Use the company's approved VPN") is not None
    assert _BULLET_PATTERN.match("- Use the company's approved VPN") is not None
    assert _BULLET_PATTERN.match("* Use the company's approved VPN") is not None
    assert _BULLET_PATTERN.match("1. Use the company's approved VPN") is not None


def test_list_items_with_continuation_lines_and_black_circle():
    lines = [
        "● Use the company's approved VPN when accessing internal systems.",
        "● Never share company credentials.",
        "● Avoid accessing confidential information through public Wi-Fi without secure",
        "protection.",
        "● Report suspected security incidents to the IT department immediately.",
    ]
    joined = _join_paragraph_lines(lines)
    expected = (
        "● Use the company's approved VPN when accessing internal systems.\n"
        "● Never share company credentials.\n"
        "● Avoid accessing confidential information through public Wi-Fi without secure protection.\n"
        "● Report suspected security incidents to the IT department immediately."
    )
    assert joined == expected


def test_real_pdf_regression_remote_work_guidelines():
    import os
    pdf_path = os.path.join(os.path.dirname(__file__), "fixtures", "remote_work_guidelines.pdf")
    assert os.path.exists(pdf_path), f"Permanent fixture missing at {pdf_path}"

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    full_text, sections = extract_text_from_pdf(pdf_bytes)
    assert len(sections) == 2

    # Verify no stacked words in extracted text
    assert "Eligible\n\nfull-time" not in full_text
    assert "Eligible\nfull-time" not in full_text

    chunks = chunk_document_sections(sections, document_name="Remote Work Guidelines.pdf")
    all_chunks_text = "\n\n".join(c["content"] for c in chunks)

    # Expected normalized target sentences
    expected_target_1 = (
        "Eligible full-time employees may work remotely up to three days per week. "
        "Employees are expected to work from the office at least two days per week, "
        "unless their manager approves an exception."
    )
    assert expected_target_1 in all_chunks_text

    expected_target_2 = "● Use the company's approved VPN when accessing internal systems."
    assert expected_target_2 in all_chunks_text

    expected_target_3 = "● Avoid accessing confidential information through public Wi-Fi without secure protection."
    assert expected_target_3 in all_chunks_text

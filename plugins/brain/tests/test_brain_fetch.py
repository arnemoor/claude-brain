import importlib.util
from pathlib import Path


HERE = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
spec = importlib.util.spec_from_file_location("brain_fetch", HERE / "scripts" / "brain_fetch.py")
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


def extract(name):
    return bf.extract_html((FIXTURES / name).read_text(encoding="utf-8"))


def test_extracts_h1_body_to_document_end():
    result = extract("fetch-article.html")
    assert result["extraction_method"] == "h1_to_document_end"
    assert "full readable source material" in result["markdown"]
    assert result["char_count"] == len(result["markdown"])
    assert "warning" not in result


def test_nav_heavy_fixture_warns_and_includes_full_page_text():
    result = extract("fetch-link-heavy.html")
    assert "warning" in result
    assert "full_page_text" in result
    assert "One" in result["full_page_text"]


def test_short_fixture_warns_and_includes_full_page_text():
    result = extract("fetch-short.html")
    assert result["char_count"] < 500
    assert "warning" in result
    assert "There is not enough content here" in result["full_page_text"]


def test_navigation_before_post_is_excluded_by_h1_slice():
    result = extract("fetch-multiple-articles.html")
    assert result["extraction_method"] == "h1_to_document_end"
    assert "The actual post starts here" in result["markdown"]
    assert "Navigation entry before" not in result["markdown"]


def test_no_h1_reports_warning_and_returns_full_text():
    result = extract("fetch-no-h1.html")
    assert result["extraction_method"] == "no_h1_found"
    assert "warning" in result
    assert result["markdown"] == result["full_page_text"]


def test_end_marker_excludes_trailing_sections():
    result = extract("fetch-end-marker.html")
    assert result["extraction_method"] == "h1_to_end_marker"
    assert "The post body ends before this marker" in result["markdown"]
    assert "Trailing subscription section" not in result["markdown"]

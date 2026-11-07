"""Phase 3: HTML extraction for the NGA content index."""

from __future__ import annotations

from app.sitegraph.extract import extract_html

PAGE = "https://dspmuranchi.ac.in/cms/hostel"


def _html(body: str, title: str = "Hostel") -> str:
    return (
        f"<html><head><title>{title} | DSPMU</title></head><body>"
        "<nav>Home About</nav>"
        "<header>Site header</header>"
        f"<main><h1>{title}</h1>{body}</main>"
        "<footer>Footer</footer>"
        "<script>alert('no')</script>"
        "</body></html>"
    )


def test_extracts_main_content_and_links():
    extracted = extract_html(
        _html(
            "<p>Boys and girls hostels with mess and Wi-Fi.</p>"
            "<table><tr><th>Fee</th><td>5000</td></tr></table>"
            '<a href="/uploads/hostel-rules.pdf">Hostel rules</a>'
            '<a href="https://example.org/apply">Apply</a>'
        ),
        PAGE,
    )

    assert extracted.title == "Hostel"
    assert "Boys and girls hostels" in extracted.text
    assert "Home About" not in extracted.text
    assert "Site header" not in extracted.text
    assert "Footer" not in extracted.text
    assert "alert" not in extracted.text
    assert extracted.char_count == len(extracted.text)
    assert extracted.tables
    assert extracted.tables[0]["rows"][0] == ["Fee", "5000"]
    urls = {link["url"] for link in extracted.links}
    assert "https://dspmuranchi.ac.in/uploads/hostel-rules.pdf" in urls
    assert "https://example.org/apply" in urls
    assert [doc["url"] for doc in extracted.documents] == [
        "https://dspmuranchi.ac.in/uploads/hostel-rules.pdf"
    ]
    assert extracted.sections[0]["heading"] == "Hostel"
    assert "Boys and girls hostels" in extracted.sections[0]["text"]


def test_content_hash_is_stable_and_content_sensitive():
    first = extract_html(_html("<p>Same text.</p>"), PAGE)
    second = extract_html(_html("<p>Same text.</p>"), PAGE)
    changed = extract_html(_html("<p>Different text.</p>"), PAGE)

    assert first.content_hash == second.content_hash
    assert first.content_hash != changed.content_hash


def test_empty_page_yields_no_sections_and_an_empty_hash():
    extracted = extract_html(
        "<html><head><title>Empty</title></head><body>"
        "<nav>Home</nav><main></main></body></html>",
        PAGE,
    )

    assert extracted.text == ""
    assert extracted.char_count == 0
    assert extracted.sections == ()
    assert extracted.content_hash


def test_hindi_content_is_detected():
    extracted = extract_html(
        _html("<p>छात्रावास की जानकारी।</p>", title="छात्रावास"),
        PAGE,
    )

    assert extracted.language == "hi"
    assert "छात्रावास" in extracted.text

"""D3 multimodal ingestion: a scanned PDF or a photographed image must
render into page images `render_pages()` can hand to the vision-capable
ModelGateway, and the extraction must come back schema-valid (or fail
closed) — never silently pass through free-form text, same guardrail the
telemetry/agent pipeline already enforces."""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.document_ingest import (  # noqa: E402
    DocumentExtraction,
    extract_document,
    extract_document_from_text,
    extract_text,
    render_pages,
)
from reo_common.model_gateway import MockModelGateway  # noqa: E402


def _tiny_pdf_bytes() -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


def _tiny_png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (64, 64), color=(200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_render_pages_from_pdf_returns_one_png_per_page():
    pages = render_pages("maintenance-notice.pdf", _tiny_pdf_bytes())
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG")


def test_render_pages_from_image_returns_single_page():
    pages = render_pages("storm-alert.jpg", _tiny_png_bytes())
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG")


def test_render_pages_rejects_unsupported_suffix():
    with pytest.raises(ValueError, match="unsupported document type"):
        render_pages("readings.csv", b"asset_id,metric,value\n")


def test_extract_document_returns_schema_valid_result_via_mock_gateway():
    pages = render_pages("inspection-report.png", _tiny_png_bytes())
    result = extract_document(
        MockModelGateway(), tenant_id="tenant-1", correlation_id="doc:test", filename="inspection-report.png", pages=pages,
    )
    assert isinstance(result, DocumentExtraction)
    assert result.document_type in (
        "maintenance_notice", "storm_alert", "weather_advisory", "grid_outage_notice", "inspection_report", "other",
    )
    assert result.severity in ("informational", "advisory", "warning", "critical")


def _tiny_docx_bytes(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_extract_text_reads_docx_paragraphs_in_order():
    content = _tiny_docx_bytes(["MAINTENANCE NOTICE", "Site: Highland Wind Farm 1", "Severity: warning"])
    text = extract_text("maintenance-notice.docx", content)
    assert text == "MAINTENANCE NOTICE\nSite: Highland Wind Farm 1\nSeverity: warning"


def test_extract_text_rejects_unsupported_suffix():
    with pytest.raises(ValueError, match="unsupported text document type"):
        extract_text("readings.csv", b"asset_id,metric,value\n")


def test_extract_text_rejects_empty_docx():
    with pytest.raises(ValueError, match="no extractable text"):
        extract_text("empty.docx", _tiny_docx_bytes([]))


def test_extract_document_from_text_returns_schema_valid_result_via_mock_gateway():
    text = "MAINTENANCE NOTICE\nSite: Highland Wind Farm 1\nSeverity: warning"
    result = extract_document_from_text(
        MockModelGateway(), tenant_id="tenant-1", correlation_id="doc:test", filename="maintenance-notice.docx", text=text,
    )
    assert isinstance(result, DocumentExtraction)
    assert result.severity in ("informational", "advisory", "warning", "critical")

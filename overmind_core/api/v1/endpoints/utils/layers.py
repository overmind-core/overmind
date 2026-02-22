"""
Utility functions for the layers endpoint.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
import logging
from typing import Optional
import pymupdf

logger = logging.getLogger(__name__)


def extract_text_from_txt(data: bytes) -> str:
    """
    Extract text from a plain text file.

    Args:
        data: Raw bytes from the text file

    Returns:
        Decoded text string
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore")


def extract_text_from_docx(data: bytes) -> str:
    """
    Extract text from a DOCX file.

    Args:
        data: Raw bytes from the DOCX file

    Returns:
        Extracted text from the document
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            with z.open("word/document.xml") as doc_xml:
                tree = ET.parse(doc_xml)
                root = tree.getroot()
                # Collect text
                texts = []
                for node in root.iter():
                    if node.tag.endswith("}t") or node.tag.endswith("}tab"):
                        if node.text:
                            texts.append(node.text)
                return "".join(texts)
        except KeyError:
            return ""


def extract_text_from_pdf(data: bytes) -> Optional[str]:
    """
    Extract text from a PDF file.

    Args:
        data: Raw bytes from the PDF file

    Returns:
        Extracted text from all pages, or None if extraction fails
    """
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages_text = []
    for page in doc:
        try:
            pages_text.append(page.get_text())
        except Exception:
            logger.exception("Error extracting text from PDF page")
    return "\n".join(pages_text)

"""
OCR and PII obfuscation functions for images and PDFs using AWS Textract and Comprehend.
"""

from collections import Counter
import copy
import io
import logging
from PIL import Image, ImageDraw, ImageFont
import pymupdf
from functools import lru_cache
from fastapi import HTTPException
from overmind_core.config import settings
from overmind_core.overmind.other_services import deidentify

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def get_textract_client():
    """Get or create a cached AWS Textract client."""
    try:
        import boto3
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "AWS SDK (boto3) is not installed. Install it with "
                "'pip install boto3' to use AWS Textract for OCR text extraction."
            ),
        )
    try:
        return boto3.client("textract", region_name=settings.aws_region)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "AWS credentials are not configured. To use AWS Textract for OCR, "
                "provide valid AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) "
                "and set AWS_REGION. Error: " + str(e)
            ),
        )


def ocr_extract_text_and_words(
    file_bytes: bytes, is_pdf: bool = False
) -> list[tuple[Image.Image, str, list[dict]]]:
    """
    Extracts text from a PDF or image file bytes using Amazon Textract.

    Args:
        file_bytes: The raw bytes of the file
        is_pdf: Whether the file is a PDF (if True, converts to images first)

    Returns:
        A list of tuples, one for each page.
        Each tuple contains: (pil_image, full_text_string, word_map)

        'word_map' is a list used to map Comprehend's character offsets
        back to Textract's 'WORD' blocks.
    """

    page_results = []
    textract_client = get_textract_client()

    # Step 1: Get images from bytes
    pil_images = []
    if is_pdf:
        # Convert PDF bytes to a list of PIL Images
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        for page_num, page in enumerate(doc):  # type: ignore
            # Render page to a pixmap with high DPI for better OCR
            pix = page.get_pixmap(dpi=200)
            pil_image = pix.pil_image()
            pil_images.append(pil_image)
    else:
        # Load single image from bytes
        pil_images = [Image.open(io.BytesIO(file_bytes))]

    # Step 2: Process each page with Textract
    for page_num, pil_image in enumerate(pil_images):
        # Convert PIL image to bytes for Textract API
        img_byte_arr = io.BytesIO()
        # Convert RGBA to RGB if necessary (common with PNGs)
        if pil_image.mode == "RGBA":
            pil_image = pil_image.convert("RGB")
        pil_image.save(img_byte_arr, format="JPEG")
        img_bytes = img_byte_arr.getvalue()

        try:
            response = textract_client.detect_document_text(Document={"Bytes": img_bytes})
        except Exception as e:
            err_name = type(e).__name__
            if "Credential" in err_name or "NoCredentials" in err_name or "AccessDenied" in str(e):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "AWS credentials are missing or invalid. To use AWS Textract for OCR, "
                        "provide valid AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) "
                        "and set AWS_REGION."
                    ),
                )
            raise

        # Step 3: Build the full_text string and the word_map
        full_text = ""
        word_map = []

        # Get all LINE blocks for reading order
        line_blocks = [b for b in response["Blocks"] if b["BlockType"] == "LINE"]
        # Create a map of all WORD blocks by their ID
        word_blocks_map = {
            b["Id"]: b for b in response["Blocks"] if b["BlockType"] == "WORD"
        }

        for line in line_blocks:
            # For each line, find its child WORD blocks
            if "Relationships" in line:
                for rel in line["Relationships"]:
                    if rel["Type"] == "CHILD":
                        for word_id in rel["Ids"]:
                            if word_id in word_blocks_map:
                                word = word_blocks_map[word_id]
                                # Store the start/end index of this word in full_text
                                start_index = len(full_text)
                                full_text += word["Text"] + " "  # Add word and space
                                end_index = len(full_text)

                                word_map.append(
                                    {
                                        "block": word,
                                        "start": start_index,
                                        "end": end_index,
                                    }
                                )

            # Add a newline at the end of each line
            full_text += "\n"

        page_results.append((pil_image, full_text, word_map))

    return page_results


def obfuscate_pii_on_image(
    image: Image.Image, pii_boxes_to_obfuscate: list[tuple[dict, str]]
) -> Image.Image:
    """
    Draws white boxes with placeholder text over PII entities on the image.

    Args:
        image: The PIL Image to obfuscate
        pii_boxes_to_obfuscate: A list of tuples: (geometry, placeholder_text)

    Returns:
        The obfuscated image
    """

    draw = ImageDraw.Draw(image)
    img_width, img_height = image.size

    for geometry, placeholder in pii_boxes_to_obfuscate:
        box = geometry["BoundingBox"]

        # Convert Textract's normalized (0-1) coordinates to absolute pixels
        left = int(box["Left"] * img_width)
        top = int(box["Top"] * img_height)
        width = int(box["Width"] * img_width)
        height = int(box["Height"] * img_height)
        right = left + width
        bottom = top + height

        # Draw the white background rectangle
        draw.rectangle([left, top, right, bottom], fill="white")

        # Use default font and render text, then scale the pixels to fit
        font = ImageFont.load_default()

        # Get the size of the text when rendered
        if hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), placeholder, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
        else:
            text_width, text_height = draw.textsize(placeholder, font=font)

        # Create a temporary image with the text
        text_image = Image.new(
            "RGBA", (int(text_width), int(text_height)), (255, 255, 255, 0)
        )
        text_draw = ImageDraw.Draw(text_image)
        text_draw.text((0, 0), placeholder, fill="black", font=font)

        # Calculate scaling to fit within the box with minimal padding
        padding = 1  # Minimal padding to avoid touching edges
        available_width = width - (2 * padding)
        available_height = height - (2 * padding)

        # Calculate scale factor to fit (maintain aspect ratio)
        scale_x = available_width / text_width if text_width > 0 else 1
        scale_y = available_height / text_height if text_height > 0 else 1
        scale = min(scale_x, scale_y, 1.0)  # Don't scale up, only down if needed

        # Resize the text image
        new_width = int(text_width * scale)
        new_height = int(text_height * scale)
        if new_width > 0 and new_height > 0:
            text_image = text_image.resize(
                (new_width, new_height), Image.Resampling.LANCZOS
            )

            # Calculate position to center the text in the box
            paste_x = left + (width - new_width) // 2
            paste_y = top + (height - new_height) // 2

            # Paste the scaled text onto the main image
            image.paste(text_image, (paste_x, paste_y), text_image)

    return image


def process_file_with_ocr_and_pii(
    file_bytes: bytes,
    engine: str,
    is_pdf: bool = False,
    info_types: list[str] | None = None,
) -> tuple[list[Image.Image], str, Counter, list[tuple[str, list[dict]]]]:
    """
    Main function to run the full OCR -> PII detection -> Obfuscation pipeline.

    Args:
        file_bytes: The raw bytes of the file to process
        engine: The engine to use for PII detection and obfuscation
        is_pdf: Whether the file is a PDF
        info_types: Optional list of PII types to redact (passed to deidentify)

    Returns:
        Tuple of:
        - list_of_obfuscated_images
        - combined_extracted_text
        - total_counter
        - list of (redacted_page_text, word_map) for each page
    """

    total_counter = Counter()

    # Step 1: Run OCR, get image(s), text, and the word map
    try:
        page_results = ocr_extract_text_and_words(file_bytes, is_pdf=is_pdf)
    except Exception as e:
        logger.error(f"Error during Textract OCR: {e}")
        raise

    if not page_results:
        logger.warning("No pages were extracted from the file")
        return [], "", total_counter, []

    obfuscated_images = []
    all_page_texts = []
    page_text_data = []  # Store (redacted_text, word_map) for each page

    for page_num, (page_image, full_text, word_map) in enumerate(page_results):
        all_page_texts.append(full_text)

        if not full_text.strip():
            logger.info(f"Page {page_num + 1} has no text, keeping original image")
            obfuscated_images.append(page_image)
            page_text_data.append(("", []))
            continue

        # Step 2: Run PII detection using the existing deidentify function
        # which handles chunking automatically and returns entity positions
        try:
            redacted_text, entity_counter, pii_entities = deidentify(
                input_str=full_text, engine=engine, info_types=info_types
            )

            # If no PII found, use original text and add the original image
            if entity_counter is None or sum(entity_counter.values()) == 0:
                logger.info(f"Page {page_num + 1}: No PII found")
                obfuscated_images.append(page_image)
                page_text_data.append((full_text, word_map))
                continue

            total_counter += entity_counter

            # Map PII entities back to WORD bounding boxes and create redacted word map
            pii_boxes_to_obfuscate = []
            pii_word_indices = set()  # Track which words contain PII

            for entity in pii_entities:
                pii_start = entity["BeginOffset"]
                pii_end = entity["EndOffset"]
                placeholder = f"[{entity['Type']}]"

                # Find all words that overlap with the PII entity's character range
                matching_words = [
                    (idx, word)
                    for idx, word in enumerate(word_map)
                    if max(pii_start, word["start"]) < min(pii_end, word["end"])
                ]

                for idx, word in matching_words:
                    pii_boxes_to_obfuscate.append(
                        (word["block"]["Geometry"], placeholder)
                    )
                    pii_word_indices.add(idx)

            logger.info(
                f"Page {page_num + 1}: Found {len(pii_boxes_to_obfuscate)} PII boxes to obfuscate"
            )

            # Create a redacted word map with placeholders for PII words
            redacted_word_map = []
            for idx, word_info in enumerate(word_map):
                if idx in pii_word_indices:
                    # Find which entity this word belongs to and use its placeholder
                    for entity in pii_entities:
                        pii_start = entity["BeginOffset"]
                        pii_end = entity["EndOffset"]
                        if max(pii_start, word_info["start"]) < min(
                            pii_end, word_info["end"]
                        ):
                            # Create a deep copy of the word block with redacted text
                            redacted_block = copy.deepcopy(word_info["block"])
                            redacted_block["Text"] = f"[{entity['Type']}]"
                            redacted_word_map.append(
                                {
                                    "block": redacted_block,
                                    "start": word_info["start"],
                                    "end": word_info["end"],
                                }
                            )
                            break
                else:
                    # Keep original word
                    redacted_word_map.append(word_info)

            # Step 3: Run obfuscation
            obfuscated_image = obfuscate_pii_on_image(
                page_image.copy(), pii_boxes_to_obfuscate
            )
            obfuscated_images.append(obfuscated_image)
            page_text_data.append((redacted_text, redacted_word_map))

        except Exception as e:
            logger.error(
                f"Error during PII detection/obfuscation on page {page_num + 1}: {e}"
            )
            # On error, return the original image and text
            obfuscated_images.append(page_image)
            page_text_data.append((full_text, word_map))

    # Combine all text from all pages
    combined_text = "\n\n".join(all_page_texts)

    return obfuscated_images, combined_text, total_counter, page_text_data


def create_pdf_with_text_overlay(
    images: list[Image.Image],
    page_text_data: list[tuple[str, list[dict]]] | None = None,
    debug_visible_text: bool = False,
) -> bytes:
    """
    Creates a PDF from images with optional text overlay for searchability.

    Args:
        images: List of PIL Images
        page_text_data: Optional list of (redacted_text, word_map) tuples for each page.
                       If provided, adds invisible text overlay at word positions.
        debug_visible_text: If True, makes the text visible for debugging purposes

    Returns:
        The PDF bytes
    """
    if not images:
        raise ValueError("No images provided")

    # Create a new PDF document
    pdf_doc = pymupdf.open()

    for page_idx, image in enumerate(images):
        # Convert PIL image to bytes
        img_byte_arr = io.BytesIO()
        if image.mode == "RGBA":
            image = image.convert("RGB")
        image.save(img_byte_arr, format="JPEG")
        img_bytes = img_byte_arr.getvalue()

        # Create a new page with the image dimensions
        img_width, img_height = image.size
        page = pdf_doc.new_page(width=img_width, height=img_height)

        # Add text layer FIRST (it will be behind the image)
        if page_text_data and page_idx < len(page_text_data):
            redacted_text, word_map = page_text_data[page_idx]

            if word_map:
                logger.info(
                    f"Adding text layer with {len(word_map)} words to page {page_idx + 1}"
                )

                # Use TextWriter to add text to the page
                tw = pymupdf.TextWriter(page.rect)

                # Add each word as text at its position
                for word_info in word_map:
                    word_block = word_info["block"]
                    word_text = word_block["Text"]
                    geometry = word_block["Geometry"]
                    box = geometry["BoundingBox"]

                    # Convert Textract's normalized coordinates to PDF coordinates
                    x0 = box["Left"] * img_width
                    y0 = box["Top"] * img_height
                    word_height = box["Height"] * img_height

                    # Calculate font size based on word height
                    # Typical ratio is about 0.7-0.8 of the box height
                    fontsize = word_height * 0.75

                    # Position is at the baseline (bottom-left of text)
                    # Add offset to account for descenders
                    text_x = x0
                    text_y = y0 + word_height * 0.8

                    try:
                        # Append text to the writer
                        tw.append(
                            pos=(text_x, text_y),
                            text=word_text + " ",
                            fontsize=fontsize,
                            font=pymupdf.Font("helv"),  # Use Helvetica (built-in font)
                        )
                    except Exception as e:
                        logger.debug(f"Could not add text for word '{word_text}': {e}")

                # Write text to page (will be behind the image we add next)
                if debug_visible_text:
                    tw.write_text(
                        page, color=(1, 0, 0)
                    )  # Red visible text for debugging (no opacity needed)
                    logger.info(
                        f"Added VISIBLE text layer to page {page_idx + 1} (debug mode)"
                    )
                else:
                    # Use black text - it will be hidden behind the image but still searchable
                    tw.write_text(page, color=(0, 0, 0))
                    logger.info(
                        f"Added text layer to page {page_idx + 1} (will be behind image)"
                    )

        # Insert the image on top of the text layer
        page.insert_image(page.rect, stream=img_bytes)

    # Save to bytes
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    return pdf_bytes


def images_to_bytes(
    images: list[Image.Image],
    output_format: str = "pdf",
    page_text_data: list[tuple[str, list[dict]]] | None = None,
    debug_visible_text: bool = False,
) -> bytes:
    """
    Converts a list of PIL images to bytes in the specified format.

    Args:
        images: List of PIL Images
        output_format: Either "pdf" for multi-page PDF or an image format like "png", "jpeg"
        page_text_data: Optional list of (redacted_text, word_map) tuples for each page.
                       Only used when output_format is "pdf". Adds text overlay for searchability.
        debug_visible_text: If True, makes the text visible for debugging purposes

    Returns:
        The file bytes
    """
    if not images:
        raise ValueError("No images provided")

    if output_format.lower() == "pdf":
        # If text data is provided, use the advanced PDF creation with text overlay
        if page_text_data:
            return create_pdf_with_text_overlay(
                images, page_text_data, debug_visible_text
            )

        # Otherwise, use the simple PIL method
        output = io.BytesIO()
        if len(images) == 1:
            images[0].save(output, format="PDF")
        else:
            images[0].save(
                output, format="PDF", save_all=True, append_images=images[1:]
            )
        return output.getvalue()
    else:
        # Save as single image (only first image if multiple)
        output = io.BytesIO()
        if len(images) > 1:
            logger.warning(
                f"Multiple images provided but output format is {output_format}, using only first image"
            )
        images[0].save(output, format=output_format.upper())
        return output.getvalue()

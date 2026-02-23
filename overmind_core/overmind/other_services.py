from collections import Counter
import re
import logging
from functools import lru_cache

import requests
from overmind_core.config import settings
from fastapi import HTTPException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AWS Comprehend limits
MAX_CHUNK_SIZE_BYTES = 95000  # AWS limit is 100,000 bytes, use 95k for safety
MAX_TOTAL_SIZE_BYTES = 50 * 1024 * 1024  # 50MB overall limit


NERPA_PII_TYPES = {
    "LOCATION": "Address, country, city, postcode, street, any other location",
    "AGE": "Age of a person",
    "DIGITAL_KEYS": "Digital keys, passwords, pins used to access anything like servers, banks, APIs, accounts etc",
    "BANK_ACCOUNT_DETAILS": "Bank account details such as number, IBAN, SWIFT, routing numbers etc",
    "CARD_DETAILS": "Debit or credit card details such as card number, CVV, expiration etc",
    "DATE_TIME": "Generic date and time",
    "DATE_OF_BIRTH": "Date of birth",
    "PERSONAL_ID_NUMBERS": "Common personal identification numbers such as passport numbers, driving licenses, taxpayer and insurance numbers",
    "TECHNICAL_ID_NUMBERS": "IP and MAC addresses, serial numbers and any other technical ID numbers",
    "EMAIL": "Email",
    "PERSON_NAME": "Person name",
    "BUSINESS_NAME": "Business name",
    "PHONE": "Any personal or other phone numbers",
    "URL": "Any short or full URL",
    "USERNAME": "Username",
    "VEHICLE_ID_NUMBERS": "Any vehicle numbers like license places, vehicle identification numbers",
}

AWS_PII_TO_NERPA_MAPPING = {
    "ADDRESS": "LOCATION",
    "AGE": "AGE",
    "AWS_ACCESS_KEY": "DIGITAL_KEYS",
    "AWS_SECRET_KEY": "DIGITAL_KEYS",
    "BANK_ACCOUNT_NUMBER": "BANK_ACCOUNT_DETAILS",
    "BANK_ROUTING": "BANK_ACCOUNT_DETAILS",
    "CREDIT_DEBIT_CVV": "CARD_DETAILS",
    "CREDIT_DEBIT_EXPIRY": "CARD_DETAILS",
    "CREDIT_DEBIT_NUMBER": "CARD_DETAILS",
    "DATE_TIME": "DATE_TIME",
    "DRIVER_ID": "PERSONAL_ID_NUMBERS",
    "EMAIL": "EMAIL",
    "INTERNATIONAL_BANK_ACCOUNT_NUMBER": "BANK_ACCOUNT_DETAILS",
    "IP_ADDRESS": "TECHNICAL_ID_NUMBERS",
    "LICENSE_PLATE": "VEHICLE_ID_NUMBERS",
    "MAC_ADDRESS": "TECHNICAL_ID_NUMBERS",
    "NAME": "PERSON_NAME",
    "PASSPORT_NUMBER": "PERSONAL_ID_NUMBERS",
    "PASSWORD": "DIGITAL_KEYS",
    "PHONE": "PHONE",
    "PIN": "DIGITAL_KEYS",
    "SSN": "PERSONAL_ID_NUMBERS",
    "SWIFT_CODE": "BANK_ACCOUNT_DETAILS",
    "UK_NATIONAL_INSURANCE_NUMBER": "PERSONAL_ID_NUMBERS",
    "UK_UNIQUE_TAXPAYER_REFERENCE_NUMBER": "PERSONAL_ID_NUMBERS",
    "URL": "URL",
    "USERNAME": "USERNAME",
    "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER": "PERSONAL_ID_NUMBERS",
    "VEHICLE_IDENTIFICATION_NUMBER": "VEHICLE_ID_NUMBERS",
}


# Simple, common PII regex patterns
PII_PATTERNS = {
    "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "PHONE": r"(\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}",
}


def merge_overlapping_entities(entities: list[dict]) -> list[dict]:
    """
    Merges overlapping entities into single entities.
    Prioritizes the entity with the longest span.

    Args:
        entities: List of entity dicts with BeginOffset and EndOffset

    Returns:
        List of merged entities sorted by BeginOffset
    """
    if not entities:
        return []

    # Sort by start position
    sorted_entities = sorted(entities, key=lambda x: x["BeginOffset"])
    merged = []

    current = sorted_entities[0]

    for next_entity in sorted_entities[1:]:
        # Check for overlap
        if next_entity["BeginOffset"] < current["EndOffset"]:
            # Overlap detected
            # Determine new range
            new_begin = min(current["BeginOffset"], next_entity["BeginOffset"])
            new_end = max(current["EndOffset"], next_entity["EndOffset"])

            # Determine which type to keep (longest span wins)
            current_len = current["EndOffset"] - current["BeginOffset"]
            next_len = next_entity["EndOffset"] - next_entity["BeginOffset"]

            kept_type = (
                current["Type"] if current_len >= next_len else next_entity["Type"]
            )
            kept_score = max(current.get("Score", 0), next_entity.get("Score", 0))

            current = {
                "Type": kept_type,
                "BeginOffset": new_begin,
                "EndOffset": new_end,
                "Score": kept_score,
            }
        else:
            # No overlap, push current and move to next
            merged.append(current)
            current = next_entity

    merged.append(current)
    return merged


@lru_cache(maxsize=None)
def get_comprehend_client():
    try:
        import boto3
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "AWS SDK (boto3) is not installed. Install it with "
                "'pip install boto3' to use AWS Comprehend for PII detection."
            ),
        )
    try:
        return boto3.client("comprehend", region_name=settings.aws_region)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "AWS credentials are not configured. To use AWS Comprehend for PII detection, "
                "provide valid AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) and "
                "set AWS_REGION. Error: " + str(e)
            ),
        )


def chunk_text_by_bytes(
    text: str, max_chunk_size: int = MAX_CHUNK_SIZE_BYTES
) -> list[str]:
    """
    Chunks text into segments that don't exceed max_chunk_size bytes.
    Tries to split at sentence boundaries (full stops), then spaces, then character boundaries.

    Args:
        text: The input text to chunk
        max_chunk_size: Maximum size in bytes for each chunk

    Returns:
        List of text chunks

    Raises:
        HTTPException: If the total text size exceeds MAX_TOTAL_SIZE_BYTES
    """
    text_bytes = text.encode("utf-8")
    total_size = len(text_bytes)

    # Check overall size limit
    if total_size > MAX_TOTAL_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Input text size ({total_size} bytes) exceeds maximum allowed size ({MAX_TOTAL_SIZE_BYTES} bytes / 50MB)",
        )

    # If text fits in one chunk, return it as-is
    if total_size <= max_chunk_size:
        return [text]

    chunks = []
    remaining_text = text

    while remaining_text:
        # If remaining text fits in one chunk, add it and break
        remaining_bytes = remaining_text.encode("utf-8")
        if len(remaining_bytes) <= max_chunk_size:
            chunks.append(remaining_text)
            break

        # Binary search to find the maximum number of characters that fit within byte limit
        # Start with a better estimate (assuming average 2 bytes per char)
        low = 1
        high = min(len(remaining_text), max_chunk_size)  # Can't exceed either bound
        best_fit = 1

        while low <= high:
            mid = (low + high) // 2
            test_bytes = remaining_text[:mid].encode("utf-8")

            if len(test_bytes) <= max_chunk_size:
                best_fit = mid
                low = mid + 1
            else:
                high = mid - 1

        # Now find a good break point, preferring sentence > word > character boundaries
        candidate_text = remaining_text[:best_fit]
        split_point = best_fit

        # Try to split at a sentence boundary (. ! ? followed by space/newline or at end)
        sentence_ends = []
        for pattern in [". ", ".\n", ".\r", "! ", "!\n", "!\r", "? ", "?\n", "?\r"]:
            pos = candidate_text.rfind(pattern)
            if pos != -1:
                sentence_ends.append(pos + len(pattern))

        if sentence_ends:
            last_sentence = max(sentence_ends)
            # Only use sentence boundary if it's not too far back (in latter half)
            if last_sentence > best_fit * 0.5:
                split_point = last_sentence

        # If no good sentence boundary, try to split at a space
        if split_point == best_fit:
            last_space = candidate_text.rfind(" ")
            # Only use space if it's not too far back
            if last_space > best_fit * 0.3:
                split_point = last_space + 1

        # Extract chunk (Python string slicing guarantees valid Unicode)
        chunk_text = remaining_text[:split_point]
        chunks.append(chunk_text)
        remaining_text = remaining_text[split_point:]

    logger.info(
        f"Chunked text into {len(chunks)} chunks (total size: {total_size} bytes)"
    )
    return chunks


def call_comprehend(text: str) -> list[dict]:
    language_code = "en"
    comprehend = get_comprehend_client()

    try:
        response = comprehend.detect_pii_entities(Text=text, LanguageCode=language_code)
    except Exception as e:
        err_name = type(e).__name__
        if "Credential" in err_name or "NoCredentials" in err_name or "AccessDenied" in str(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    "AWS credentials are missing or invalid. To use AWS Comprehend for PII "
                    "detection, provide valid AWS credentials (AWS_ACCESS_KEY_ID, "
                    "AWS_SECRET_ACCESS_KEY) and set AWS_REGION."
                ),
            )
        raise

    pii_entities = response.get("Entities", [])

    formatted_entities = []
    for entity in pii_entities:
        formatted_entities.append(
            {
                "Type": AWS_PII_TO_NERPA_MAPPING.get(entity["Type"], entity["Type"]),
                "BeginOffset": entity["BeginOffset"],
                "EndOffset": entity["EndOffset"],
                "Score": entity.get("Score", 0),
            }
        )

    return formatted_entities


def call_nerpa(text: str, info_types: dict[str, str] | None = None) -> list[dict]:
    if not settings.nerpa_base_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "Nerpa inference server is not configured. Set the NERPA_BASE_URL "
                "environment variable to a valid Nerpa endpoint, or switch DLP engine "
                "to 'aws' by setting DEFAULT_DLP_ENGINE=aws and providing AWS credentials."
            ),
        )
    try:
        response = requests.post(
            f"{settings.nerpa_base_url}/api/v1/inference",
            json={"text": text, "entities": info_types},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["Entities"]
    except requests.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Cannot connect to Nerpa inference server at {settings.nerpa_base_url}. "
                "Ensure the server is running and reachable, or switch DLP engine to 'aws' "
                "by setting DEFAULT_DLP_ENGINE=aws."
            ),
        )


def _process_single_chunk(
    chunk_text: str, engine: str, info_types: dict[str, str] | None = None
) -> tuple[str, Counter, list[dict]]:
    """
    Processes a single chunk of text for PII detection and redaction.

    Args:
        chunk_text: A single chunk of text to process (must be under AWS size limit)
        info_types: Optional list of PII types to redact. If None, all types are redacted.

    Returns:
        Tuple of (redacted_chunk_text, counter_of_entities, list_of_entities_with_offsets)
    """
    if engine == "aws":
        formatted_entities = call_comprehend(chunk_text)
    elif engine == "nerpa":
        formatted_entities = call_nerpa(chunk_text, info_types)
    else:
        raise ValueError(f"Invalid engine: {engine}")

    # We only return PII types we explicitly support for now, regardless of engine. Unless user specifies some other types which engine would also return.
    info_types = info_types if info_types else NERPA_PII_TYPES

    # Run regex on original text
    for entity_type, pattern in PII_PATTERNS.items():
        # Find all matches
        for match in re.finditer(pattern, chunk_text):
            formatted_entities.append(
                {
                    "Type": entity_type,
                    "BeginOffset": match.start(),
                    "EndOffset": match.end(),
                    "Score": 1.0,  # Regex is exact match
                }
            )

    if not formatted_entities:
        return chunk_text, Counter(), []

    # Merge overlapping entities
    merged_entities = merge_overlapping_entities(formatted_entities)

    # Sort entities by offset in reverse order for redaction
    sorted_entities = sorted(
        merged_entities, key=lambda e: e["EndOffset"], reverse=True
    )

    redacted_text = chunk_text
    entity_counter = Counter()
    entities_with_offsets = []

    for entity in sorted_entities:
        entity_type = entity["Type"]
        entity_counter[entity_type] += 1

        # Store entity information
        entities_with_offsets.append(entity)

        if entity_type in info_types:
            redacted_text = (
                redacted_text[: entity["BeginOffset"]]
                + f"[{entity_type}]"
                + redacted_text[entity["EndOffset"] :]
            )

    return redacted_text, entity_counter, entities_with_offsets


def deidentify(
    input_str: str, engine: str, info_types: dict[str, str] | None = None
) -> tuple[str, Counter, list[dict]]:
    """
    Detects and redacts PII entities from input text using AWS Comprehend.
    Automatically chunks large text to handle AWS API limits.

    Args:
        input_str: The input text to process
        info_types: Optional list of PII types to redact. If None, all types are redacted.

    Returns:
        Tuple of (redacted_text, counter_of_entities, list_of_entities_with_adjusted_offsets)
    """
    # Chunk the text if needed
    chunks = chunk_text_by_bytes(input_str)

    # Process each chunk
    redacted_chunks = []
    total_entity_counter = Counter()
    all_entities = []
    char_offset = 0

    for i, chunk_text in enumerate(chunks):
        try:
            redacted_chunk, chunk_counter, chunk_entities = _process_single_chunk(
                chunk_text=chunk_text, engine=engine, info_types=info_types
            )
            redacted_chunks.append(redacted_chunk)
            total_entity_counter += chunk_counter

            # Adjust entity offsets for the full text and add to the list
            for entity in chunk_entities:
                adjusted_entity = entity.copy()
                adjusted_entity["BeginOffset"] += char_offset
                adjusted_entity["EndOffset"] += char_offset
                all_entities.append(adjusted_entity)

            char_offset += len(chunk_text)

        except Exception as e:
            logger.error(f"Error processing chunk {i + 1}/{len(chunks)}: {str(e)}")
            raise

    # Concatenate all redacted chunks
    redacted_text = "".join(redacted_chunks)

    if sum(total_entity_counter.values()) == 0:
        logger.info("No PII entities found.")
        return input_str, None, []

    logger.info(
        f"Redacted {sum(total_entity_counter.values())} PII entities across {len(chunks)} chunk(s)"
    )
    return redacted_text, total_entity_counter, all_entities

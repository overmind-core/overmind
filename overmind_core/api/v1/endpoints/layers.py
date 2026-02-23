from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from overmind_core.db.session import get_db
from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind_core.api.v1.endpoints.utils.layers import (
    extract_text_from_txt,
    extract_text_from_docx,
    extract_text_from_pdf,
)
from opentelemetry import trace
from overmind_core.config import settings, setup_opentelemetry
from overmind_core.overmind.layers import run_overmind_layer
from overmind_core.overmind.llms import SUPPORTED_LLM_MODEL_NAMES
from overmind_core.api.v1.helpers.permissions import ProjectPermission
from pydantic import BaseModel, field_validator
from typing import Any, Literal
import logging
import base64
from overmind_core.overmind.ocr_pii import process_file_with_ocr_and_pii, images_to_bytes

logger = logging.getLogger(__name__)


router = APIRouter()


class RunLayerRequest(BaseModel):
    """
    Request payload for /layers/run.
    """

    input_data: str
    layer_position: Literal["input", "output"]
    policies: list[Any] | None = None
    kwargs: dict[str, Any] | None = None
    model_name: str | None = None

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in SUPPORTED_LLM_MODEL_NAMES:
            allowed = ", ".join(sorted(SUPPORTED_LLM_MODEL_NAMES))
            raise ValueError(f"Unsupported model_name: {v}. Supported: {allowed}")
        return v


class RunLayerResponse(BaseModel):
    """
    Response returned by /layers/run (the first element of run_overmind_layer tuple).
    """

    policy_results: dict[str, dict[str, Any]]
    overall_policy_outcome: str
    processed_data: str | None = None
    span_context: dict[str, str]


class ProcessFileResponse(BaseModel):
    """
    Response returned by /layers/process-file.
    """

    policy_results: dict[str, dict[str, Any]]
    overall_policy_outcome: str
    processed_data: str | None = None
    span_context: dict[str, str]
    processed_file: dict[str, Any]  # Changed from dict[str, str] to support bytes


@router.post("/run", response_model=RunLayerResponse)
async def run_layer(
    payload: RunLayerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Execute a set of policies on input data and return the processed result.
    Policies can be referenced by ID or passed as transient objects with parameters.
    """
    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()
    project_id = current_user.token.project_id if current_user.token else None
    auth_context = await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.ADD_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    org_policy_provider = request.app.state.org_policy_provider

    # Get user/token permissions from auth context (enterprise) or empty set (core)
    user_permissions: set[str] = (
        getattr(auth_context, "USER_PERMISSIONS", None) or set()
    )

    # Extract input data and policies from payload
    input_data = payload.input_data
    layer_position = payload.layer_position

    # Currently we don't support merging policies or otherwise combining them
    policies_from_org = await org_policy_provider.get_org_llm_policies(
        db=db, current_user=current_user
    )

    # MANAGE_TOKENS permission means the token can override org policies with
    # request-supplied ones (e.g. in developer workflows).
    if (
        policies_from_org
        and (policies_from_org.get("input") or policies_from_org.get("output"))
        and ProjectPermission.MANAGE_TOKENS.value not in user_permissions
    ):
        policies = policies_from_org.get(layer_position, [])
    else:
        policies = payload.policies or []

    if not policies:
        raise HTTPException(
            status_code=400,
            detail="No policies found for the organisation and no policies provided in the request",
        )

    if not input_data:
        raise HTTPException(status_code=400, detail="Input data must be provided")

    current_provider = trace.get_tracer_provider()
    if not hasattr(current_provider, "add_span_processor"):
        setup_opentelemetry()

    tracer = trace.get_tracer("overmind.layers")

    extra_kwargs = payload.kwargs or {}
    if payload.model_name:
        extra_kwargs = {**extra_kwargs, "model_name": payload.model_name}

    result_dict, _ = run_overmind_layer(
        input_data, policies, current_user, db, tracer, **extra_kwargs
    )
    return result_dict


@router.post("/process-file", response_model=ProcessFileResponse)
async def upload_and_process_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a single file, extract text (txt, docx, pdf, or images),
    run input-layer policies to hide sensitive data, and return processed file.

    For images (jpg, jpeg, png, gif, webp): Uses OCR and PII obfuscation.
    For PDFs: Attempts normal extraction first; if no text found, uses OCR.
    For text files: Standard text extraction and processing.
    """

    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()
    project_id = current_user.token.project_id if current_user.token else None
    await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.ADD_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    filename = file.filename
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()

    # Extended allowed file types to include images
    allowed_exts = {
        "txt",
        "docx",
        "pdf",
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "csv",
        "tsv",
    }
    image_exts = {"jpg", "jpeg", "png", "webp"}

    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_exts)}",
        )

    # Read file content into memory
    raw_bytes = await file.read()

    # Determine processing path based on file type
    input_text = ""
    output_file_bytes = None
    output_media_type = "text/plain; charset=utf-8"
    output_extension = "txt"
    needs_ocr = False

    if ext in image_exts:
        # Images always need OCR
        logger.info(f"Processing image file with OCR: {filename}")
        needs_ocr = True

    elif ext == "pdf":
        # Try normal PDF text extraction first
        logger.info(f"Attempting normal PDF text extraction: {filename}")
        input_text = extract_text_from_pdf(raw_bytes)

        if not input_text or not input_text.strip():
            # No text found, need OCR
            logger.info("No text found in PDF, switching to OCR")
            needs_ocr = True
        else:
            logger.info(f"Extracted {len(input_text)} characters from PDF")

    elif ext == "txt" or ext == "csv" or ext == "tsv":
        input_text = extract_text_from_txt(raw_bytes)
    elif ext == "docx":
        input_text = extract_text_from_docx(raw_bytes)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    # Fetch policies
    org_policy_provider = request.app.state.org_policy_provider
    policies_from_org = await org_policy_provider.get_org_llm_policies(
        db=db, current_user=current_user
    )
    policies = policies_from_org.get("input")
    if not policies:
        raise HTTPException(
            status_code=400, detail="No policies found for the organisation"
        )

    # Ensure tracer is set up
    current_provider = trace.get_tracer_provider()
    if not hasattr(current_provider, "add_span_processor"):
        setup_opentelemetry()
    tracer = trace.get_tracer("overmind.layers")

    # Process based on whether OCR is needed
    if needs_ocr:
        # Use OCR and PII obfuscation pipeline
        try:
            logger.info("Running OCR and PII obfuscation pipeline")

            anonymize_pii_policy = next(
                (
                    policy
                    for policy in policies
                    if policy["policy_template"] == "anonymize_pii"
                ),
                None,
            )
            obfuscated_images, extracted_text, total_counter, page_text_data = (
                process_file_with_ocr_and_pii(
                    file_bytes=raw_bytes,
                    engine=anonymize_pii_policy.get("parameters", {}).get(
                        "engine", settings.default_dlp_engine
                    )
                    if anonymize_pii_policy
                    else settings.default_dlp_engine,
                    is_pdf=(ext == "pdf"),
                    info_types=anonymize_pii_policy.get("parameters", {}).get(
                        "info_types"
                    )
                    if anonymize_pii_policy
                    else None,  # get info types from the policy or set to None (will anonymize all PII types)
                )
            )

            if not obfuscated_images:
                raise HTTPException(
                    status_code=500,
                    detail="OCR processing failed: no images were generated",
                )

            # This is tech debt because it won't call overmind layer properly, so no span data will be logged and returned
            # Also all PII types will be filtered regardless of user settings and no other policies will be run
            pii_detected = sum(total_counter.values()) > 0
            if pii_detected:
                policy_results = {
                    "anonymize_pii": {
                        "result": "altered",
                        "policy_results": {
                            "PII_detected": True,
                            "PII_types": total_counter,
                        },
                    }
                }

            else:
                policy_results = {
                    "anonymize_pii": {
                        "result": "passed",
                        "policy_results": {"PII_detected": False},
                    }
                }

            result_dict = {
                "policy_results": policy_results,
                "overall_policy_outcome": "altered" if pii_detected else "passed",
                "processed_data": extracted_text,
                "span_context": {},
            }

            # Convert obfuscated images to bytes with text overlay for searchable PDFs
            if ext == "pdf" or len(obfuscated_images) > 1:
                # Multi-page or PDF: return as PDF with text overlay
                output_file_bytes = images_to_bytes(
                    obfuscated_images,
                    output_format="pdf",
                    page_text_data=page_text_data,
                )
                output_media_type = "application/pdf"
                output_extension = "pdf"
            else:
                # Single image: return in original format (no text overlay for images)
                output_extension = ext if ext != "jpg" else "jpeg"
                output_file_bytes = images_to_bytes(
                    obfuscated_images, output_format=output_extension
                )
                output_media_type = f"image/{output_extension}"

        except Exception as e:
            logger.exception("Error during OCR processing")
            raise HTTPException(
                status_code=500, detail=f"OCR processing failed: {str(e)}"
            )
    else:
        # Standard text processing
        if not input_text:
            raise HTTPException(
                status_code=400,
                detail="Could not extract any text from the uploaded file",
            )

        result_dict, _ = run_overmind_layer(
            input_text, policies, current_user, db, tracer
        )
        processed_text = result_dict.get("processed_data") or input_text
        output_file_bytes = processed_text.encode("utf-8")
        output_media_type = "text/plain; charset=utf-8"
        output_extension = "txt"

    # Prepare output filename
    base_filename = filename.rsplit(".", 1)[0] if "." in filename else filename
    output_filename = f"{base_filename}_processed.{output_extension}"

    # Encode binary content as base64 for JSON serialization
    if needs_ocr:
        # Binary image/PDF data needs base64 encoding
        encoded_content = base64.b64encode(output_file_bytes).decode("utf-8")
    else:
        # Text data can be returned as-is (already a string from .decode('utf-8'))
        encoded_content = output_file_bytes.decode("utf-8")

    # Return result with processed file
    result_dict["processed_file"] = {
        "filename": output_filename,
        "content": encoded_content,  # base64-encoded for binary, utf-8 string for text
        "media_type": output_media_type,
        "is_binary": needs_ocr,  # Flag to indicate if content is binary
    }

    return ProcessFileResponse(
        policy_results=result_dict["policy_results"],
        overall_policy_outcome=result_dict["overall_policy_outcome"],
        processed_data=result_dict.get("processed_data"),
        span_context=result_dict["span_context"],
        processed_file=result_dict["processed_file"],
    )

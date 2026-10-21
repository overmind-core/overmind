"""Offline OCR language policy shared by the API and isolated parser."""

from __future__ import annotations

LANGUAGES = {
    "en": "eng",
    "fr": "fra",
    "de": "deu",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "pl": "pol",
    "tr": "tur",
    "ru": "rus",
    "uk": "ukr",
    "ar": "ara",
    "hi": "hin",
    "zh-cn": "chi_sim",
    "zh-tw": "chi_tra",
    "ja": "jpn",
    "ko": "kor",
    "el": "ell",
}
POLICY_VERSION = 4


def validate_languages(value) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 4
        or any(not isinstance(item, str) or item not in LANGUAGES.values() for item in value)
    ):
        raise ValueError(
            "Supply up to four supported Tesseract language codes, or [] for the English default."
        )
    return list(dict.fromkeys(value))

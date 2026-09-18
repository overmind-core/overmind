from pathlib import Path

from django.conf import settings

from overbae.settings import _HOSTED_STATIC_CDN, _file_storages

_FS = "django.core.files.storage.FileSystemStorage"
_S3 = "storages.backends.s3boto3.S3Boto3Storage"


def test_storages_default_is_filesystem_unless_bucket_set():
    assert settings.STORAGES["default"]["BACKEND"] == _FS

    local = _file_storages(media_root=Path("/media"), media_url="media/", region="eu-west-1")
    assert local["default"]["BACKEND"] == _FS

    hosted = _file_storages(
        media_root=Path("/media"),
        media_url="media/",
        region="eu-west-1",
        s3_bucket="hosted-media",
    )
    assert hosted["default"]["BACKEND"] == _S3
    assert hosted["default"]["OPTIONS"]["bucket_name"] == "hosted-media"


def test_hosted_static_cdn_default_kept():
    assert _HOSTED_STATIC_CDN == "static.overmindlab.ai"

import pytest
from conftest import frozen_dataset
from factories import auth_client, make_project, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.covers("api-compression")]


def test_json_is_compressed_and_an_event_stream_is_not():
    user = make_user()
    project = make_project(member=user)
    dataset = frozen_dataset(project, user=user)
    client = auth_client(user)
    detail = client.get(f"/api/datasets/{dataset.pk}/", HTTP_ACCEPT_ENCODING="gzip")
    assert detail["Content-Encoding"] == "gzip"
    stream = client.get(
        f"/api/datasets/{dataset.pk}/events/",
        HTTP_ACCEPT_ENCODING="gzip",
        HTTP_ACCEPT="text/event-stream",
    )
    assert stream.get("Content-Encoding") is None

import pytest
from conftest import frozen_dataset
from factories import auth_client, make_project, make_user
from fakes.llm import tool_call

from overbae.services import sandbox_sessions
from overbae.services.assistant import loop, threads

pytestmark = [pytest.mark.django_db, pytest.mark.covers("assistant-results")]

COMPUTE = """\
import json
import matplotlib.pyplot as plt
json.dump([{"class": "first", "rows": 3}, {"class": "third", "rows": 5}], open("classes.json", "w"))
plt.bar(["first", "third"], [3, 5])
plt.savefig("classes.png")
open("notes.txt", "w").write("not an image")
"""


def present(image):
    return tool_call(
        "present",
        {
            "root": {
                "type": "Stack",
                "children": [
                    {
                        "type": "Chart",
                        "title": "Rows",
                        "kind": "bar",
                        "data": "classes",
                        "x": "class",
                        "y": ["rows"],
                    },
                    {"type": "Image", "title": "Plot", "image": "plot"},
                ],
            },
            "tables": {"classes": "classes.json"},
            "images": {"plot": image},
        },
    )


def test_a_result_carries_the_kernels_rows_and_image_and_refuses_a_file_that_is_not_one(fake_llm):
    user = make_user()
    project = make_project(member=user)
    thread = threads.open_thread(project, "dataset", frozen_dataset(project, user=user).pk)
    fake_llm.stream_rounds(
        [
            ([tool_call("run_python", {"code": COMPUTE})], ""),
            ([present("notes.txt")], ""),
            ([present("classes.png")], ""),
            ([], "Shown."),
        ]
    )
    threads.send(thread, user, request_key="first", text="Show the classes.")
    try:
        while loop.advance(thread.pk):
            pass
    finally:
        thread.refresh_from_db()
        sandbox_sessions.provider().stop(thread.session)

    computed, refused, shown = thread.events.filter(kind="tool_result")
    assert computed.payload["ok"], computed.payload
    assert not refused.payload["ok"]
    assert "PNG, JPEG or WebP" in refused.payload["content"]["error"]
    result = shown.presentation
    assert result["data"]["classes"] == [
        {"class": "first", "rows": 3},
        {"class": "third", "rows": 5},
    ]
    url = f"/api/assistant-threads/{thread.pk}/images/{result['images']['plot']}/"
    image = auth_client(user).get(url)
    assert image.status_code == 200 and b"".join(image.streaming_content).startswith(b"\x89PNG")
    assert auth_client(make_user()).get(url).status_code == 404

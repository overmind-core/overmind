import pytest

pytestmark = pytest.mark.covers("http-api")


def test_project_surfaces_render_without_browser_or_server_errors(console, workshop):
    project = workshop.project()["id"]
    for route, text in [
        ("datasets", "Datasets"),
        ("training", "Train model"),
        ("evaluations", "Evaluations"),
        ("inference", "Inference"),
        ("optimiser", "Optimiser"),
    ]:
        console.goto(f"/{route}?projectId={project}")
        console.sees(text)
        console.healthy()
        console.save_evidence(f"project-surface-{route}")

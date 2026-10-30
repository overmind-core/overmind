import uuid

from overbae.models import Dataset, DeployedModel, FinetuningJob


def test_standalone_trained_model_copies_its_setup_prompt(console, rest_for, account_key, mcp_for):
    project = rest_for(account_key).request(
        "POST", "/api/projects/", json={"name": "Standalone model", "slug": "standalone-model"}
    )["id"]
    group = uuid.uuid4()
    job = FinetuningJob.objects.create(
        project_id=project,
        dataset=Dataset.objects.create(project_id=project, name="Standalone training data"),
        base_model="Qwen/Qwen2.5-14B-Instruct",
        status="succeeded",
        group_id=group,
        output_model_name="ft-standalone-browser",
    )
    model = DeployedModel.objects.create(
        project_id=project,
        finetuning_job=job,
        model_id="ft-standalone-browser",
        status="ready",
        max_model_len=8192,
    )
    console._browser.call(
        console._page.context.grant_permissions, ["clipboard-read", "clipboard-write"]
    )
    console._browser.call(console._page.evaluate, "navigator.clipboard.writeText('')")
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job.id}")
    console.press("Copy a prompt switching to ft-standalone-browser")
    console.sees("Prompt copied")
    console.save_evidence("standalone-model-setup-prompt")
    prompt = console._browser.call(console._page.evaluate, "navigator.clipboard.readText()")
    assert model.model_id in prompt
    result = mcp_for(account_key).call(
        "get_model_swap_prompt", {"project_id": project, "finetune": str(job.id)}
    )
    assert model.model_id in result["prompt"]
    assert result["pin"] is True
    assert result["capability_id"] is None
    assert len(result["resource_links"]) == 1

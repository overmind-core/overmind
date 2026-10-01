from ..datasets import tickets
from ..stack import drain


def test_the_console_shows_the_synced_agent_and_its_traces(
    console, cli, mcp_for, sample_agent, live_api, support_desk_llm, worker
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    project = mcp_for(cli.project_key(sample_agent)).project()["id"]
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)

    console.goto(f"/capabilities?projectId={project}")
    for truth in sample_agent.truth["capabilities"].values():
        console.sees(truth["name"])

    console.goto(f"/observability?projectId={project}")
    console.click("Root traces")
    console.sees("handle_ticket")


def test_a_file_uploaded_in_the_console_becomes_a_usable_dataset(
    console, cli, mcp_for, sample_agent, worker, tmp_path
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))
    project = mcp.project()["id"]

    console.goto(f"/datasets?projectId={project}")
    console.press("New dataset")
    console.click("Upload file")
    console.fill("#dataset-name", "Console upload")
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.choose("#dataset-purpose", "Evaluation")
    console.press("Create dataset")
    drain(worker)

    [dataset] = [
        d for d in mcp.call("list_datasets", {})["datasets"] if d["name"] == "Console upload"
    ]
    inspected = mcp.call("inspect_dataset", {"dataset": dataset["id"]})
    assert inspected["active"]["rows"] == 3
    assert inspected["intent"] == "eval"

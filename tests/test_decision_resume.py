import copy
import random

import pytest

torch = pytest.importorskip("torch")


def optimizer_model():
    model = torch.nn.Sequential(torch.nn.Linear(4, 6), torch.nn.Dropout(0.3), torch.nn.Linear(6, 2))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.02)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=8)
    return model, optimizer, scheduler


def update(model, optimizer, scheduler):
    inputs = torch.randn(7, 4) * random.uniform(0.8, 1.2)
    targets = torch.rand(7, 2).softmax(-1)
    optimizer.zero_grad()
    loss = -(targets * model(inputs).log_softmax(-1)).sum(-1).mean()
    loss.backward()
    optimizer.step()
    scheduler.step()
    return loss.detach().clone()


def test_resume_preserves_adam_schedule_rng_and_exact_next_updates(tmp_path):
    from modal_shared.decision_checkpoint import restore_resume, save_resume

    torch.manual_seed(913)
    random.seed(913)
    model, optimizer, scheduler = optimizer_model()
    for _ in range(3):
        update(model, optimizer, scheduler)
    path = tmp_path / "resume.pt"
    signature = {"base_identity": "sealed-base", "data": "rows", "steps": 8}
    save_resume(path, model, optimizer, scheduler, signature=signature, step=3, tokens_seen=61)
    losses = [update(model, optimizer, scheduler) for _ in range(5)]
    final = copy.deepcopy(model.state_dict())
    expected_rng = torch.rand(3)
    expected_python = random.random()

    torch.manual_seed(123)
    random.seed(123)
    restored, new_optimizer, new_scheduler = optimizer_model()
    position = restore_resume(path, restored, new_optimizer, new_scheduler, signature=signature)
    assert position == (3, 61)
    for expected in losses:
        torch.testing.assert_close(
            update(restored, new_optimizer, new_scheduler), expected, atol=0, rtol=0
        )
    for name, parameter in restored.state_dict().items():
        torch.testing.assert_close(parameter, final[name], atol=0, rtol=0)
    torch.testing.assert_close(torch.rand(3), expected_rng, atol=0, rtol=0)
    assert random.random() == expected_python


@pytest.mark.parametrize("change", ["base", "missing", "extra", "shape", "dtype"])
def test_resume_rejects_changed_identity_and_incomplete_parameters_before_mutating(
    tmp_path, change
):
    from modal_shared.decision_checkpoint import restore_resume, save_resume

    model, optimizer, scheduler = optimizer_model()
    path = tmp_path / "resume.pt"
    signature = {"base_identity": "sealed-base", "data": "rows"}
    save_resume(path, model, optimizer, scheduler, signature=signature, step=0, tokens_seen=0)
    saved = torch.load(path, weights_only=True)
    name = next(iter(saved["parameters"]))
    if change == "base":
        signature = {**signature, "base_identity": "replaced-base"}
    elif change == "missing":
        saved["parameters"].pop(name)
    elif change == "extra":
        saved["parameters"]["unknown"] = torch.tensor([1.0])
    elif change == "shape":
        saved["parameters"][name] = torch.zeros(1)
    else:
        saved["parameters"][name] = saved["parameters"][name].double()
    torch.save(saved, path)
    original = copy.deepcopy(model.state_dict())
    with pytest.raises(ValueError):
        restore_resume(path, model, optimizer, scheduler, signature=signature)
    for key, value in model.state_dict().items():
        torch.testing.assert_close(value, original[key], atol=0, rtol=0)


def test_base_identity_refuses_mutated_sealed_weights(tmp_path):
    from modal_shared.decision_checkpoint import training_base_identity
    from modal_shared.serving.artifacts import seal_base

    (tmp_path / "config.json").write_text("{}")
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"original bytes")
    manifest = seal_base(tmp_path, "pinned/model")
    assert training_base_identity(tmp_path, "pinned/model") == manifest["identity"]
    with pytest.raises(ValueError, match="repository"):
        training_base_identity(tmp_path, "different/model")
    weights.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="changed"):
        training_base_identity(tmp_path, "pinned/model")

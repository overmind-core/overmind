import json
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import cpu_allocation_client as client


@pytest.mark.parametrize("wrong_storage", [False, True])
def test_storage_comparison_reuses_pool_and_secret_for_both_arms(monkeypatch, wrong_storage):
    monkeypatch.setattr(
        "sys.argv",
        [
            "client",
            "--model",
            "base",
            "--stream-memory-gb",
            "8",
            "--cpu",
            "8",
            "--rounds",
            "4",
            "--storage-mode",
            "volume",
            "s3",
            "s3",
            "volume",
            "--s3-index",
            "s3://index",
        ],
    )
    monkeypatch.setattr(
        client,
        "configuration",
        Mock(return_value={"serve_image": "vllm", "gpu": "H200", "adapters": ["a"]}),
    )
    cls, secret = Mock(), Mock()
    monkeypatch.setattr(client.modal.Cls, "from_name", Mock(return_value=cls))
    monkeypatch.setattr(client.modal.Secret, "from_name", Mock(return_value=secret))
    monkeypatch.setattr(client.time, "sleep", Mock())
    calls = []

    def probe(worker, cfg, options):
        calls.append((worker, dict(options)))
        return {
            "storage_mode": "wrong" if wrong_storage else options["storage_mode"],
            "reload": {
                "results": [
                    {
                        "cpu_threads_active": 8,
                        "cpu_threads_original": 8,
                        "cpu_threads_restored": 8,
                        "shard_target_bytes": 2 * 1024**3,
                        "s3": {"index_uri": options["s3_index"]} if options["s3_index"] else None,
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "client_first_token_probe", probe)
    if wrong_storage:
        with pytest.raises(RuntimeError, match="Worker storage"):
            client.main()
        return
    client.main()
    cls.with_options.assert_called_once()
    assert cls.with_options.call_args.kwargs["secrets"] == [secret]
    assert len({id(worker) for worker, _ in calls}) == 1
    assert [options["storage_mode"] for _, options in calls] == ["volume", "s3", "s3", "volume"]
    assert [options["s3_index"] for _, options in calls] == [None, "s3://index", "s3://index", None]
    assert "s3" not in cls.with_options.return_value.call_args.kwargs["config_json"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--storage-mode", "s3"],
        ["--s3-index", "s3://index"],
        ["--storage-mode", "s3", "--s3-index", "s3://index", "--copy-mode", "prefetched"],
        ["--storage-mode", "s3", "--s3-index", "s3://index", "--artifact-shard-gib", "1"],
    ],
)
def test_invalid_s3_comparison_fails_before_modal_lookup(monkeypatch, extra):
    monkeypatch.setattr(
        "sys.argv", ["client", "--model", "base", "--stream-memory-gb", "8", *extra]
    )
    lookup = Mock()
    monkeypatch.setattr(client.modal.Cls, "from_name", lookup)
    with pytest.raises(SystemExit):
        client.main()
    lookup.assert_not_called()


@pytest.mark.parametrize("mismatch", [None, "region", "cloud", "missing", "other_region"])
@pytest.mark.parametrize("suffix", [1, 2])
def test_placement_sequence_reuses_handles_and_checks_actual_placement(
    monkeypatch, capsys, mismatch, suffix
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "client",
            "--model",
            "base",
            "--stream-memory-gb",
            "16",
            "--cpu",
            "8",
            "--rounds",
            "4",
            "--cloud",
            "aws",
            "--region-sequence",
            "us-east",
            "us-west",
            "us-west",
            "us-east",
        ],
    )
    cfg = {"serve_image": "vllm", "gpu": "B200", "adapters": ["tenant"]}
    monkeypatch.setattr(client, "configuration", Mock(return_value=cfg))
    handles = [object(), object()]
    factories = [Mock(return_value=handle) for handle in handles]
    cls = Mock()
    cls.with_options.side_effect = factories
    monkeypatch.setattr(client.modal.Cls, "from_name", Mock(return_value=cls))

    def result(handle, cfg, options):
        environment = {
            "MODAL_REGION": "us-east-9"
            if mismatch == "region"
            else f"us-{'east' if handles.index(handle) == 0 else 'west'}-{suffix}",
            "MODAL_CLOUD_PROVIDER": "CLOUD_PROVIDER_GCP"
            if mismatch == "cloud"
            else "CLOUD_PROVIDER_AWS",
        }
        if mismatch == "other_region":
            environment["MODAL_REGION"] = "us-west-1"
        return {
            "runtime_environment": {} if mismatch == "missing" else environment,
            "reload": {
                "results": [
                    {
                        "cpu_threads_active": 8,
                        "cpu_threads_original": 8,
                        "cpu_threads_restored": 8,
                        "shard_target_bytes": 2 * 1024**3,
                    }
                ]
            },
        }

    probe = Mock(side_effect=result)
    monkeypatch.setattr(client, "client_first_token_probe", probe)
    monkeypatch.setattr(client.time, "sleep", Mock())
    if mismatch:
        with pytest.raises(RuntimeError, match="worker region/cloud"):
            client.main()
        assert probe.call_count == 1
    else:
        client.main()
        assert [call.args[0] for call in probe.call_args_list] == [
            handles[0],
            handles[1],
            handles[1],
            handles[0],
        ]
        assert [call.kwargs["region"] for call in cls.with_options.call_args_list] == [
            "us-east",
            "us-west",
        ]
        assert all(call.kwargs["cloud"] == "aws" for call in cls.with_options.call_args_list)
        assert all(call.kwargs["cpu"] == 8 for call in cls.with_options.call_args_list)
        assert factories[0].call_args.kwargs == factories[1].call_args.kwargs
        assert json.loads(factories[0].call_args.kwargs["config_json"]) == {
            "serve_image": "vllm",
            "gpu": "B200",
        }
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    accepted = [row for row in records if "placement_verified" in row]
    assert all(row["placement_verified"] is (mismatch is None) for row in accepted)
    assert [row["requested_region"] for row in accepted] == (
        ["us-east"] if mismatch else ["us-east", "us-west", "us-west", "us-east"]
    )


@pytest.mark.parametrize(
    "options",
    [
        ["--cloud", "aws", "--region-sequence", "us-east-1"],
        ["--cloud", "gcp", "--region-sequence", "us-east"],
        ["--region-sequence", "us-east"],
    ],
)
def test_region_sequence_rejects_unsupported_selectors_before_lookup(monkeypatch, options):
    monkeypatch.setattr(
        "sys.argv", ["client", "--model", "base", "--stream-memory-gb", "16", *options]
    )
    lookup = Mock()
    monkeypatch.setattr(client.modal.Cls, "from_name", lookup)
    with pytest.raises(SystemExit):
        client.main()
    lookup.assert_not_called()


@pytest.mark.parametrize("wrong_size", [False, True])
def test_shard_size_changes_artifact_pool_not_adapter_or_reload_options(
    monkeypatch, capsys, wrong_size
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "client",
            "--model",
            "base",
            "--stream-memory-gb",
            "8",
            "--cpu",
            "8",
            "--rounds",
            "4",
            "--artifact-shard-gib",
            "2",
            "1",
            "4",
            "1",
        ],
    )
    cfg = {"serve_image": "vllm", "gpu": "H200", "adapters": ["tenant-a", "tenant-b"]}
    monkeypatch.setattr(client, "configuration", Mock(return_value=cfg))
    cls = Mock()
    handles = [object(), object(), object()]
    factories = [Mock(return_value=handle) for handle in handles]
    cls.with_options.side_effect = factories
    monkeypatch.setattr(client.modal.Cls, "from_name", Mock(return_value=cls))
    probe = Mock(
        side_effect=lambda handle, cfg, options: {
            "reload": {
                "results": [
                    {
                        "cpu_threads_active": 8,
                        "cpu_threads_original": 8,
                        "cpu_threads_restored": 8,
                        "shard_target_bytes": (
                            4 if wrong_size else [2, 1, 4][handles.index(handle)]
                        )
                        * 1024**3,
                    }
                ]
            }
        }
    )
    monkeypatch.setattr(client, "client_first_token_probe", probe)
    monkeypatch.setattr(client.time, "sleep", Mock())
    if wrong_size:
        with pytest.raises(RuntimeError, match="artifact shard size differs"):
            client.main()
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert records[-1]["artifact_size_verified"] is False
        assert probe.call_count == 1
        return
    client.main()
    assert [call.args[0] for call in probe.call_args_list] == [*handles, handles[1]]
    assert all(call.args[1] is cfg for call in probe.call_args_list)
    assert all("artifact_shard_gib" not in call.args[2] for call in probe.call_args_list)
    assert [json.loads(factory.call_args.kwargs["config_json"]) for factory in factories] == [
        {"serve_image": "vllm", "gpu": "H200"},
        {"serve_image": "vllm", "gpu": "H200", "artifact_shard_gib": 1},
        {"serve_image": "vllm", "gpu": "H200", "artifact_shard_gib": 4},
    ]
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(row["artifact_size_verified"] for row in records if "thread_control_verified" in row)
    assert [row["artifact_shard_gib"] for row in records if "thread_control_verified" in row] == [
        2,
        1,
        4,
        1,
    ]


@pytest.mark.parametrize("placement", [[], ["--cloud", "aws", "--region", "us-west"]])
@pytest.mark.parametrize("original_threads", [8, 4])
@pytest.mark.parametrize("copy_modes", [[], ["--copy-mode", "native-pinned", "prefetched"]])
@pytest.mark.parametrize("policies", [[], ["--cpu-policy", "request-only", "capped"]])
@pytest.mark.parametrize(
    "shard_orders", [[], ["--shard-order", "smallest-first", "largest-first", "manifest"]]
)
def test_resource_trials_reuse_probe_and_hold_threads_fixed(
    monkeypatch, capsys, placement, original_threads, copy_modes, policies, shard_orders
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "client",
            "--model",
            "base",
            "--stream-memory-gb",
            "8",
            "--rounds",
            "4",
            *placement,
            *copy_modes,
            *policies,
            *shard_orders,
        ],
    )
    cfg = {"serve_image": "vllm", "gpu": "H200", "adapters": ["a", "b"]}
    config = Mock(return_value=cfg)
    monkeypatch.setattr(client, "configuration", config)
    cls = Mock()
    lookup = Mock(return_value=cls)
    monkeypatch.setattr(client.modal.Cls, "from_name", lookup)

    def result(worker, cfg, options):
        limit = cls.with_options.call_args.kwargs["cpu"]
        return {
            "client_ttft_s": 12.0,
            "reload": {
                "results": [
                    {
                        "cpu_threads_original": original_threads,
                        "shard_target_bytes": 2 * 1024**3,
                        "cpu_threads_active": 8,
                        "cpu_threads_restored": original_threads,
                        "cpu_affinity": list(range(limit[1] if isinstance(limit, tuple) else 24)),
                        "cpu_limits": {
                            "/sys/fs/cgroup/cpu.max": f"{limit[1] * 100000} 100000"
                            if isinstance(limit, tuple)
                            else "max 100000"
                        },
                    }
                ]
            },
        }

    probe = Mock(side_effect=result)
    monkeypatch.setattr(client, "client_first_token_probe", probe)
    monkeypatch.setattr(client.time, "sleep", Mock())
    if original_threads != 8:
        with pytest.raises(RuntimeError, match="hold Torch threads"):
            client.main()
    else:
        client.main()
    config.assert_called_once_with("base", 32768)
    lookup.assert_called_once_with(client.APP_NAME, "H200Worker", environment_name="overmind-dev")
    assert [call.kwargs["cpu"] for call in cls.with_options.call_args_list] == (
        ([8, (16, 16), 16, (8, 8)] if policies else [8, 16]) if original_threads == 8 else [8]
    )
    for call in cls.with_options.call_args_list:
        assert call.kwargs == {
            "cpu": call.kwargs["cpu"],
            "env": dict.fromkeys(
                ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"), "8"
            ),
            **({"cloud": "aws", "region": "us-west"} if placement else {}),
        }
    assert json.loads(cls.with_options.return_value.call_args.kwargs["config_json"]) == {
        "serve_image": "vllm",
        "gpu": "H200",
    }
    assert probe.call_args.args[2] == {
        "concurrency": 16,
        "copy_mode": "prefetched" if copy_modes and original_threads == 8 else "native-pinned",
        "cpu_threads": 8,
        "metadata_mode": "cached",
        "stream_memory_gb": 8,
        "storage_mode": "volume",
        "shard_order": "smallest-first" if shard_orders else "manifest",
    }
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    expected_orders = (
        ["smallest-first", "largest-first", "manifest", "smallest-first"]
        if shard_orders
        else ["manifest"] * 4
    )
    assert [row["shard_order"] for row in records if "shard_order" in row] == (
        expected_orders if original_threads == 8 else expected_orders[:1]
    )
    assert [row["copy_mode"] for row in records if "copy_mode" in row] == (
        (["native-pinned", "prefetched"] * 2 if copy_modes else ["native-pinned"] * 4)
        if original_threads == 8
        else ["native-pinned"]
    )
    assert records[-1]["thread_control_verified"] is (original_threads == 8)
    assert records[-1]["cpu_guest_quota_matches_limit"] is (
        True if policies and original_threads == 8 else None
    )
    assert records[-1]["cpu_affinity_matches_limit"] is (
        True if policies and original_threads == 8 else None
    )
    assert [row["cpu_requested"] for row in records if "thread_control_verified" in row] == (
        [8, 16, 16, 8] if original_threads == 8 else [8]
    )


@pytest.mark.parametrize(
    "limits,expected",
    [
        ({"/sys/fs/cgroup/cpu.max": "800000 100000"}, True),
        ({"/sys/fs/cgroup/cpu.max": "max 100000"}, False),
        ({"/sys/fs/cgroup/cpu.max": "1600000 100000"}, False),
        ({"/sys/fs/cgroup/cpu.max": "0 0"}, False),
        ({"/sys/fs/cgroup/cpu.max": "invalid"}, False),
        ({}, False),
        (
            {
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "800000",
                "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
            },
            True,
        ),
        (
            {
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1",
                "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
            },
            False,
        ),
    ],
)
def test_cpu_limit_requires_visible_matching_quota(limits, expected):
    assert client.cpu_quota_matches(limits, 8) is expected


@pytest.mark.parametrize("quota", ["800000 100000", "max 100000"])
@pytest.mark.parametrize("affinity", [list(range(8)), list(range(24)), None])
def test_capped_trials_require_affinity_and_report_guest_quota_separately(
    monkeypatch, capsys, quota, affinity
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "client",
            "--model",
            "base",
            "--stream-memory-gb",
            "8",
            "--cpu",
            "8",
            "--rounds",
            "4",
            "--cpu-policy",
            "capped",
            "request-only",
            "capped",
            "capped",
        ],
    )
    monkeypatch.setattr(
        client, "configuration", Mock(return_value={"serve_image": "vllm", "gpu": "H200"})
    )
    capped, request_only = object(), object()
    cls = Mock()
    cls.with_options.side_effect = [Mock(return_value=capped), Mock(return_value=request_only)]
    monkeypatch.setattr(client.modal.Cls, "from_name", Mock(return_value=cls))
    probe = Mock(
        return_value={
            "reload": {
                "results": [
                    {
                        "cpu_threads_original": 8,
                        "shard_target_bytes": 2 * 1024**3,
                        "cpu_threads_active": 8,
                        "cpu_threads_restored": 8,
                        "cpu_limits": {"/sys/fs/cgroup/cpu.max": quota},
                        "cpu_affinity": affinity,
                    }
                ]
            }
        }
    )
    monkeypatch.setattr(client, "client_first_token_probe", probe)
    monkeypatch.setattr(client.time, "sleep", Mock())
    if affinity != list(range(8)):
        with pytest.raises(RuntimeError, match="does not match the worker CPU affinity"):
            client.main()
        assert probe.call_count == 1
    else:
        client.main()
        assert [call.args[0] for call in probe.call_args_list] == [
            capped,
            request_only,
            capped,
            capped,
        ]
        assert [call.kwargs["cpu"] for call in cls.with_options.call_args_list] == [(8, 8), 8]
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["cpu_guest_quota_matches_limit"] is (not quota.startswith("max"))
    assert records[-1]["cpu_affinity_matches_limit"] is (affinity == list(range(8)))
    assert "cpu_limit_verified" not in records[-1]


@pytest.mark.parametrize(
    "affinity,expected",
    [
        (list(range(8)), True),
        ([1] * 8, False),
        ([True] * 8, False),
        ([-1] + list(range(7)), False),
        (None, False),
        ([], False),
    ],
)
def test_cpu_affinity_requires_distinct_valid_cpus(affinity, expected):
    assert client.cpu_affinity_matches(affinity, 8) is expected

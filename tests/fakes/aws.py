from __future__ import annotations

import gzip
import hashlib
import io
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from overbae.core.aws import register_client_factory, reset_client_factory


def _client_error(code: str, message: str, operation: str = "Operation") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


@dataclass
class FakeS3:
    buckets: dict[str, dict[str, bytes]] = field(default_factory=dict)

    def ensure_bucket(self, bucket: str) -> dict[str, bytes]:
        return self.buckets.setdefault(bucket, {})

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        if bucket not in self.buckets:
            raise _client_error("404", "NoSuchBucket", "HeadBucket")
        return {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        self.ensure_bucket(bucket)[key.lstrip("/")] = data
        return {"ETag": '"fake-etag"'}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        store = self.buckets.get(bucket, {})
        key = kwargs["Key"].lstrip("/")
        if key not in store:
            raise _client_error("NoSuchKey", f"Key not found: {key}", "GetObject")
        data = store[key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        store = self.buckets.get(bucket, {})
        key = kwargs["Key"].lstrip("/")
        if key not in store:
            raise _client_error("404", f"NotFound: {key}", "HeadObject")
        return {"ContentLength": len(store[key])}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        store = self.buckets.get(bucket, {})
        prefix = str(kwargs.get("Prefix", "")).lstrip("/")
        contents = [
            {"Key": k, "Size": len(v)} for k, v in sorted(store.items()) if k.startswith(prefix)
        ]
        return {"Contents": contents, "KeyCount": len(contents)}

    def generate_presigned_url(self, client_method: str, **kwargs: Any) -> str:
        _ = client_method
        params = kwargs["Params"]
        return f"https://s3.fake.aws/{params['Bucket']}/{params['Key']}?sig=fake"


@dataclass
class FakeBedrockRuntime:
    converse_calls: list[dict[str, Any]] = field(default_factory=list)
    invoke_calls: list[dict[str, Any]] = field(default_factory=list)
    next_converse_response: dict[str, Any] | None = None
    embedding_vector: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4])

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.converse_calls.append(kwargs)
        if self.next_converse_response is not None:
            resp = self.next_converse_response
            self.next_converse_response = None
            return resp
        tool_cfg = kwargs.get("toolConfig") or {}
        tool_choice = (tool_cfg.get("toolChoice") or {}).get("tool") or {}
        if tool_choice.get("name"):
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "call_schema_1",
                                    "name": tool_choice["name"],
                                    "input": {"verdict": "pass", "score": 1.0},
                                }
                            }
                        ],
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 42, "outputTokens": 18, "cacheReadInputTokens": 4},
                "ResponseMetadata": {"RequestId": "req-bedrock-schema-1"},
            }
        if tool_cfg.get("tools"):
            first_tool = tool_cfg["tools"][0]["toolSpec"]["name"]
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"text": "Calling tool now."},
                            {
                                "toolUse": {
                                    "toolUseId": "call_tool_1",
                                    "name": first_tool,
                                    "input": {"query": "order-42"},
                                }
                            },
                        ],
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 30, "outputTokens": 15, "cacheReadInputTokens": 0},
                "ResponseMetadata": {"RequestId": "req-bedrock-tool-1"},
            }
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Bedrock VPC completion ok."}],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 25, "outputTokens": 10, "cacheReadInputTokens": 5},
            "ResponseMetadata": {"RequestId": "req-bedrock-1"},
        }

    def invoke_model(self, **kwargs: Any) -> dict[str, Any]:
        self.invoke_calls.append(kwargs)
        payload = {"embedding": self.embedding_vector, "inputTextTokenCount": 6}
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}


@dataclass
class FakeSageMaker:
    s3: FakeS3
    training_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    processing_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    endpoint_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    inference_components: dict[str, dict[str, Any]] = field(default_factory=dict)
    auto_complete_preparation: bool = True
    auto_complete_training: bool = True

    def create_processing_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["ProcessingJobName"]
        env = kwargs.get("Environment") or {}
        prep_id = env.get("OVERMIND_PREPARATION_ID", "")
        bucket = env.get("AWS_SFT_BUCKET_NAME", "")
        self.processing_jobs[name] = {
            "ProcessingJobName": name,
            "ProcessingJobStatus": "Completed" if self.auto_complete_preparation else "InProgress",
            "Environment": env,
            "kwargs": kwargs,
        }
        if self.auto_complete_preparation and prep_id and bucket:
            raw_gz = self.s3.buckets.get(bucket, {}).get(
                f"preparations/{prep_id}/rows.jsonl.gz", b""
            )
            lines = gzip.decompress(raw_gz).decode("utf-8").splitlines() if raw_gz else []
            tokens_lines: list[str] = []
            max_tokens = 64
            for line in lines:
                if not line.strip():
                    continue
                row = json.loads(line)
                source_row = row.get("source_row", 0)
                cell = row.get("cell", "")
                from modal_shared.training_data import row_key

                key_hash = row_key(row)
                tok_entry = {
                    "key": key_hash,
                    "cell": cell,
                    "source_row": source_row,
                    "input_ids": [1, 2, 3, 4],
                    "labels": [-100, -100, 3, 4],
                    "length": 32,
                }
                tokens_lines.append(json.dumps(tok_entry))
            tokens_bytes = ("\n".join(tokens_lines) + ("\n" if tokens_lines else "")).encode(
                "utf-8"
            )
            artifact_sha = hashlib.sha256(tokens_bytes).hexdigest()
            self.s3.put_object(
                Bucket=bucket,
                Key=f"preparations/{prep_id}/tokens.jsonl",
                Body=tokens_bytes,
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"preparations/{prep_id}/tokenizer/tokenizer_config.json",
                Body=b'{"tokenizer_class": "PreTrainedTokenizerFast"}',
            )
            report = {
                "ready": True,
                "rows": len(tokens_lines),
                "incompatible_rows": 0,
                "max_tokens": max_tokens,
                "artifact_sha256": artifact_sha,
                "compute_usage": {
                    "usage_id": f"prep-{prep_id}",
                    "gpu_type": "",
                    "gpu_count": 0,
                    "elapsed_seconds": 12.0,
                    "cpu_core_seconds": 48.0,
                    "memory_gib_seconds": 48.0,
                },
            }
            self.s3.put_object(
                Bucket=bucket,
                Key=f"preparations/{prep_id}/report.json",
                Body=json.dumps(report).encode("utf-8"),
            )
        return {
            "ProcessingJobArn": f"arn:aws:sagemaker:eu-west-1:123456789012:processing-job/{name}"
        }

    def describe_processing_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["ProcessingJobName"]
        if name not in self.processing_jobs:
            raise _client_error("ResourceNotFound", name, "DescribeProcessingJob")
        return self.processing_jobs[name]

    def stop_processing_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["ProcessingJobName"]
        if name in self.processing_jobs:
            self.processing_jobs[name]["ProcessingJobStatus"] = "Stopped"
        return {}

    def create_training_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["TrainingJobName"]
        arn = f"arn:aws:sagemaker:eu-west-1:123456789012:training-job/{name}"
        env = kwargs.get("Environment") or {}
        bucket = env.get("AWS_SFT_BUCKET_NAME", "")
        self.training_jobs[name] = {
            "TrainingJobName": name,
            "TrainingJobArn": arn,
            "TrainingJobStatus": "Completed" if self.auto_complete_training else "InProgress",
            "Environment": env,
            "ResourceConfig": kwargs.get("ResourceConfig") or {},
            "kwargs": kwargs,
        }
        if self.auto_complete_training and bucket:
            metrics = [
                json.dumps(
                    {
                        "event": "BT_PROGRESS",
                        "step": 10,
                        "total_steps": 10,
                        "loss": 0.42,
                        "lr": 1e-4,
                        "token_accuracy": 0.88,
                        "epoch": 1.0,
                        "eta_s": 0,
                        "num_tokens": 4096,
                        "tokens_per_s": 512.0,
                        "elapsed_s": 8.0,
                    }
                ),
                json.dumps(
                    {
                        "event": "BT_EVAL",
                        "step": 10,
                        "epoch": 1.0,
                        "eval_loss": 0.39,
                        "eval_token_accuracy": 0.91,
                    }
                ),
            ]
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/metrics.jsonl",
                Body=("\n".join(metrics) + "\n").encode("utf-8"),
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/compute-usage.json",
                Body=json.dumps(
                    [
                        {
                            "usage_id": f"train-{name}",
                            "gpu_type": env.get("OVERMIND_GPU_TYPE", "H100"),
                            "gpu_count": int(env.get("OVERMIND_GPU_COUNT", "1")),
                            "elapsed_seconds": 60.0,
                            "cpu_core_seconds": 240.0,
                            "memory_gib_seconds": 480.0,
                        }
                    ]
                ).encode("utf-8"),
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/final/adapter_config.json",
                Body=json.dumps({"r": int(env.get("LORA_R", "16"))}).encode("utf-8"),
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/final/adapter_model.safetensors",
                Body=b"\x00" * (1 << 20),
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/final/checkpoint.json",
                Body=json.dumps({"identity": f"sha256:{name}"}).encode("utf-8"),
            )
            self.s3.put_object(
                Bucket=bucket,
                Key=f"runs/{name}/meta.json",
                Body=json.dumps(
                    {
                        "run_id": name,
                        "status": "succeeded",
                        "started_at": time.time() - 60,
                        "finished_at": time.time(),
                        "call_id": arn,
                    }
                ).encode("utf-8"),
            )
        return {"TrainingJobArn": arn}

    def describe_training_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["TrainingJobName"]
        if name not in self.training_jobs:
            raise _client_error("ResourceNotFound", name, "DescribeTrainingJob")
        return self.training_jobs[name]

    def stop_training_job(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["TrainingJobName"]
        if name in self.training_jobs:
            self.training_jobs[name]["TrainingJobStatus"] = "Stopped"
        return {}

    def create_model(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["ModelName"]
        self.models[name] = kwargs
        return {"ModelArn": f"arn:aws:sagemaker:eu-west-1:123456789012:model/{name}"}

    def create_endpoint_config(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["EndpointConfigName"]
        self.endpoint_configs[name] = kwargs
        return {
            "EndpointConfigArn": f"arn:aws:sagemaker:eu-west-1:123456789012:endpoint-config/{name}"
        }

    def create_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["EndpointName"]
        self.endpoints[name] = {
            "EndpointName": name,
            "EndpointStatus": "InService",
            **kwargs,
        }
        return {"EndpointArn": f"arn:aws:sagemaker:eu-west-1:123456789012:endpoint/{name}"}

    def describe_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["EndpointName"]
        if name not in self.endpoints:
            raise _client_error(
                "ValidationException", f"Could not find endpoint {name}", "DescribeEndpoint"
            )
        return self.endpoints[name]

    def delete_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        self.endpoints.pop(kwargs["EndpointName"], None)
        return {}

    def create_inference_component(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["InferenceComponentName"]
        copy_count = (kwargs.get("RuntimeConfig") or {}).get("CopyCount", 1)
        self.inference_components[name] = {
            "InferenceComponentName": name,
            "InferenceComponentStatus": "InService",
            "RuntimeConfig": {
                "DesiredCopyCount": copy_count,
                "CurrentCopyCount": copy_count,
            },
            **kwargs,
        }
        return {
            "InferenceComponentArn": f"arn:aws:sagemaker:eu-west-1:123456789012:inference-component/{name}"
        }

    def update_inference_component(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["InferenceComponentName"]
        comp = self.inference_components.setdefault(name, {"InferenceComponentName": name})
        copy_count = (kwargs.get("RuntimeConfig") or {}).get("CopyCount", 1)
        comp["InferenceComponentStatus"] = "InService"
        comp["RuntimeConfig"] = {
            "DesiredCopyCount": copy_count,
            "CurrentCopyCount": copy_count,
        }
        comp.update(kwargs)
        return {
            "InferenceComponentArn": f"arn:aws:sagemaker:eu-west-1:123456789012:inference-component/{name}"
        }

    def describe_inference_component(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs["InferenceComponentName"]
        if name not in self.inference_components:
            raise _client_error("ValidationException", name, "DescribeInferenceComponent")
        return self.inference_components[name]

    def delete_inference_component(self, **kwargs: Any) -> dict[str, Any]:
        self.inference_components.pop(kwargs["InferenceComponentName"], None)
        return {}


@dataclass
class FakeSageMakerRuntime:
    invocations: list[dict[str, Any]] = field(default_factory=list)
    next_response: dict[str, Any] | None = None

    def invoke_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        self.invocations.append(kwargs)
        if self.next_response is not None:
            payload = self.next_response
            self.next_response = None
            return {
                "Body": io.BytesIO(json.dumps(payload).encode("utf-8")),
                "ContentType": "application/json",
            }
        raw_body = kwargs.get("Body", b"{}")
        body = json.loads(
            raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else raw_body
        )
        if "questions" in body:
            answers = {}
            for qk, qv in (body.get("questions") or {}).items():
                options = sorted((qv.get("criteria") or {"A": "A", "B": "B"}).keys(), reverse=True)
                first = options[0]
                probs = {
                    opt: (0.85 if opt == first else round(0.15 / max(1, len(options) - 1), 4))
                    for opt in options
                }
                answers[qk] = {
                    "type": "choice",
                    "choice": first,
                    "probabilities": probs,
                    "confidence": 0.85,
                }
            resp = {
                "id": "sm-jev-1",
                "model": body.get("model") or "typesafe/jev-1.13",
                "provider": "SageMaker",
                "answers": answers,
                "usage": {"input_tokens": 20, "output_tokens": 8, "cost": 0.0001},
            }
        else:
            resp = {
                "id": "chatcmpl-sm-1",
                "model": body.get("model", "served-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "SageMaker endpoint reply."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            }
        return {
            "Body": io.BytesIO(json.dumps(resp).encode("utf-8")),
            "ContentType": "application/json",
        }

    def invoke_endpoint_with_response_stream(self, **kwargs: Any) -> dict[str, Any]:
        self.invocations.append(kwargs)
        chunk = b'data: {"choices":[{"delta":{"content":"Hello from stream"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'
        return {"Body": [{"PayloadPart": {"Bytes": chunk}}]}


@dataclass
class FakeApplicationAutoScaling:
    targets: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)

    def register_scalable_target(self, **kwargs: Any) -> dict[str, Any]:
        self.targets.append(kwargs)
        return {}

    def put_scaling_policy(self, **kwargs: Any) -> dict[str, Any]:
        self.policies.append(kwargs)
        return {"PolicyARN": "arn:aws:autoscaling:eu-west-1:123456789012:scalingPolicy:fake"}


@dataclass
class FakePricing:
    def get_products(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        items = [
            ("ml.g5.2xlarge", "1.515"),
            ("ml.g6.2xlarge", "1.12"),
            ("ml.g6e.2xlarge", "2.48"),
            ("ml.p4d.24xlarge", "32.77"),
            ("ml.p4de.24xlarge", "40.96"),
            ("ml.p5.4xlarge", "6.88"),
            ("ml.p5.48xlarge", "55.04"),
            ("ml.p5e.48xlarge", "63.30"),
        ]
        price_list = [
            json.dumps(
                {
                    "product": {"attributes": {"instanceType": inst}},
                    "terms": {
                        "OnDemand": {
                            "TERM1": {"priceDimensions": {"DIM1": {"pricePerUnit": {"USD": usd}}}}
                        }
                    },
                }
            )
            for inst, usd in items
        ]
        return {"PriceList": price_list}


@dataclass
class FakeAWS:
    s3: FakeS3 = field(default_factory=FakeS3)
    bedrock_runtime: FakeBedrockRuntime = field(default_factory=FakeBedrockRuntime)
    sagemaker: FakeSageMaker = field(init=False)
    sagemaker_runtime: FakeSageMakerRuntime = field(default_factory=FakeSageMakerRuntime)
    autoscaling: FakeApplicationAutoScaling = field(default_factory=FakeApplicationAutoScaling)
    pricing: FakePricing = field(default_factory=FakePricing)

    def __post_init__(self) -> None:
        self.sagemaker = FakeSageMaker(s3=self.s3)
        self.s3.ensure_bucket("test-ft-bucket")
        self.s3.ensure_bucket("test-sft-bucket")
        self.s3.ensure_bucket("test-weights-bucket")

    def get_caller_identity(self) -> dict[str, Any]:
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:role/overmind-task"}

    def client(self, service_name: str, *, region_name: str | None = None) -> Any:
        _ = region_name
        mapping = {
            "s3": self.s3,
            "bedrock-runtime": self.bedrock_runtime,
            "sagemaker": self.sagemaker,
            "sagemaker-runtime": self.sagemaker_runtime,
            "application-autoscaling": self.autoscaling,
            "pricing": self.pricing,
            "sts": self,
        }
        if service_name not in mapping:
            raise ValueError(f"Unsupported fake AWS service: {service_name}")
        return mapping[service_name]

    @contextmanager
    def active(self):
        register_client_factory(self.client)
        try:
            yield self
        finally:
            reset_client_factory()

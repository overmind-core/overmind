"""This process's ECS task: its identity, its scale-in protection and its AWS access.

Outside ECS there is no task, and nothing here touches the network.
"""

import json
import logging
import os
import urllib.request

import boto3
import botocore.session
from botocore.config import Config
from botocore.credentials import ContainerProvider, CredentialResolver
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Bounded so a slow endpoint stalls neither a consumer loop nor a threads-pool slot.
AWS_CONFIG = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
PROTECTION_MINUTES = 60


class Task:
    def __init__(self, arn: str, cluster: str, agent_uri: str):
        self.arn = arn
        self.region = arn.split(":")[3]
        self.cluster = cluster.rsplit("/", 1)[-1]
        self._agent_uri = agent_uri
        self._clients = {}
        self._deployment = None

    @classmethod
    def current(cls) -> "Task | None":
        metadata_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
        agent_uri = os.environ.get("ECS_AGENT_URI")
        if not metadata_uri or not agent_uri:
            return None
        with urllib.request.urlopen(f"{metadata_uri}/task", timeout=2) as response:
            metadata = json.load(response)
        return cls(metadata["TaskARN"], metadata["Cluster"], agent_uri)

    def client(self, name: str):
        if not self._clients:
            # Archive credentials are also in the container environment and would win
            # the default chain. Use the task role, which ContainerProvider refreshes.
            core = botocore.session.get_session()
            core.register_component(
                "credential_provider", CredentialResolver([ContainerProvider()])
            )
            self._session = boto3.Session(botocore_session=core)
            if self._session.get_credentials() is None:
                raise RuntimeError("Hosted workers require an ECS task role")
        if name not in self._clients:
            self._clients[name] = self._session.client(
                name, region_name=self.region, config=AWS_CONFIG
            )
        return self._clients[name]

    def protect(self, enabled: bool) -> bool:
        state = {"ProtectionEnabled": enabled}
        if enabled:
            state["ExpiresInMinutes"] = PROTECTION_MINUTES
        request = urllib.request.Request(
            f"{self._agent_uri}/task-protection/v1/state",
            data=json.dumps(state).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.load(response)
        except (OSError, ValueError) as exc:
            logger.warning("ecs_task: protection=%s refused: %s", enabled, exc)
            return False
        if "protection" not in result:
            logger.warning("ecs_task: protection=%s refused: %s", enabled, result)
            return False
        return True

    def superseded(self) -> bool | None:
        """Whether a newer revision is the service's primary deployment; None if unknown."""
        try:
            ecs = self.client("ecs")
            if self._deployment is None:
                (task,) = ecs.describe_tasks(cluster=self.cluster, tasks=[self.arn])["tasks"]
                group = task.get("group", "")
                # A one-off task (a migration) has no service to be superseded by.
                service = group.removeprefix("service:") if group.startswith("service:") else ""
                self._deployment = (service, task["taskDefinitionArn"])
            service, revision = self._deployment
            if not service:
                return False
            (description,) = ecs.describe_services(cluster=self.cluster, services=[service])[
                "services"
            ]
            primary = next(d for d in description["deployments"] if d["status"] == "PRIMARY")
        except (
            BotoCoreError,
            ClientError,
            RuntimeError,
            KeyError,
            ValueError,
            StopIteration,
        ) as exc:
            logger.warning("ecs_task: deployment lookup failed: %s", exc)
            return None
        return primary["taskDefinition"] != revision

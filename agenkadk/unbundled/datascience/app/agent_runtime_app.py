import logging
import os
import vertexai
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp
from typing import Any

from app.agent import app as adk_app

logging.basicConfig(level=logging.INFO)


class AgentEngineApp(AdkApp):
    def __init__(self, app=None, **kwargs):
        """Handle both old AdkApp API (agent=) and new API (app=)."""
        try:
            super().__init__(app=app, **kwargs)
        except TypeError:
            super().__init__(agent=app, **kwargs)

    def set_up(self) -> None:
        vertexai.init()
        super().set_up()
        logging_client = google_cloud_logging.Client()
        self.logger = logging_client.logger(__name__)
        gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    def register_operations(self) -> dict[str, list[str]]:
        operations = super().register_operations()
        return operations

    def clone(self) -> "AgentEngineApp":
        return self


_logs_bucket = os.environ.get("LOGS_BUCKET_NAME")

agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=_logs_bucket)
        if _logs_bucket
        else InMemoryArtifactService()
    ),
)

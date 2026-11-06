import asyncio
import logging
import os
import vertexai

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app

logging.basicConfig(level=logging.INFO)


class AgentEngineApp(AdkApp):
    def __init__(self, app=None, **kwargs):
        try:
            super().__init__(app=app, **kwargs)
        except TypeError:
            super().__init__(agent=app, **kwargs)

    def set_up(self) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                asyncio.set_event_loop(asyncio.new_event_loop())
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
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

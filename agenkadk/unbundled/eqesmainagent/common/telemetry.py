import logging
import os

def setup_telemetry(service_name: str | None = None) -> str | None:
    # Always enable Agent Engine's built-in telemetry collector.
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

    if service_name:
        os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )

    if bucket and capture_content not in ("false", "False", "FALSE", "0"):
        # GCS upload enabled — write GenAI spans as JSONL to the bucket.
        # Hard-cap to NO_CONTENT so prompts/responses are never stored at rest.
        logging.info(
            "GenAI telemetry GCS upload enabled — mode: NO_CONTENT "
            "(span metadata only, prompts/responses excluded)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        svc = os.environ.get("OTEL_SERVICE_NAME", "agent")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace={svc},service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
        return bucket
    else:
        logging.info(
            "GenAI telemetry GCS upload disabled. "
            "Set LOGS_BUCKET_NAME and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT to enable."
        )
        return None

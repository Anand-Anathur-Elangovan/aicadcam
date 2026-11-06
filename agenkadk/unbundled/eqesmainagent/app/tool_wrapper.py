"""Tool wrapper to add defensive error handling to all tool functions."""
import functools
import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


def safe_tool(func: Callable) -> Callable:
    """Decorator to wrap tool functions with error handling.

    Ensures tool functions always return valid dict responses and never
    cause the agent to crash.

    Args:
        func: Tool function to wrap

    Returns:
        Wrapped function with error handling
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Dict[str, Any]:
        try:
            logger.info(f"Calling tool: {func.__name__}")
            result = func(*args, **kwargs)

            # Ensure result is a dict
            if not isinstance(result, dict):
                logger.warning(
                    f"Tool {func.__name__} returned non-dict: {type(result)}"
                )
                return {
                    "status": "error",
                    "error": f"Tool {func.__name__} returned invalid response type",
                    "result": str(result),
                }

            logger.info(f"Tool {func.__name__} completed successfully")
            return result

        except Exception as exc:
            logger.exception(f"Tool {func.__name__} failed: %s", exc)
            return {
                "status": "error",
                "error": f"Tool {func.__name__} encountered an error: {str(exc)}",
                "function": func.__name__,
            }

    return wrapper


def validate_tool_response(response: Any) -> Dict[str, Any]:
    """Validate and sanitize a tool response.

    Args:
        response: Raw tool response

    Returns:
        Validated dict response
    """
    if response is None:
        logger.warning("Tool returned None, converting to error response")
        return {
            "status": "error",
            "error": "Tool returned None instead of a valid response",
        }

    if not isinstance(response, dict):
        logger.warning(f"Tool returned non-dict: {type(response)}")
        return {
            "status": "error",
            "error": f"Invalid response type: {type(response)}",
            "raw_response": str(response),
        }

    # Ensure status field exists
    if "status" not in response:
        logger.debug("Adding missing 'status' field to tool response")
        response["status"] = "success"

    return response

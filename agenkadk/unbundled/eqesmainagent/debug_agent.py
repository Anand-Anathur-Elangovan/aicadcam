"""Debug script to test the eqes orchestrator agent and see detailed errors."""
import logging
import sys

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("debug_agent.log"),
    ],
)

logger = logging.getLogger(__name__)

# Import the agent
from app.agent import root_agent

def test_simple_query():
    """Test a simple query to see if the agent works."""
    logger.info("Starting simple query test...")

    try:
        # Create a test session
        session = root_agent.create_session(user_id="test_user_123")
        logger.info(f"Created session: {session.id if hasattr(session, 'id') else session}")

        # Test query
        test_message = "Hello, what can you help me with?"
        logger.info(f"Sending test message: {test_message}")

        response_parts = []
        for event in root_agent.stream_query(
            user_id="test_user_123",
            session_id=session.id if hasattr(session, "id") else session["id"],
            message=test_message,
        ):
            logger.info(f"Received event type: {type(event)}")
            logger.debug(f"Event content: {event}")

            # Try to extract text
            if isinstance(event, dict):
                content = event.get("content", {})
                parts = content.get("parts", [])
                logger.debug(f"Parts type: {type(parts)}, Parts value: {parts}")

                if parts is None:
                    logger.error("ERROR: parts is None!")
                    continue

                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        response_parts.append(part["text"])
            else:
                # Object-style event
                content = getattr(event, "content", None)
                if content:
                    parts = getattr(content, "parts", None)
                    logger.debug(f"Parts type: {type(parts)}, Parts value: {parts}")

                    if parts is None:
                        logger.error("ERROR: parts is None!")
                        continue

                    for part in parts:
                        text = getattr(part, "text", None)
                        if text:
                            response_parts.append(text)

        response = "\n".join(response_parts)
        logger.info(f"Final response ({len(response)} chars): {response}")
        print("\n=== RESPONSE ===")
        print(response)

    except Exception as exc:
        logger.exception("Error during test: %s", exc)
        raise


def test_with_registration():
    """Test with user registration."""
    logger.info("Starting test with user registration...")

    try:
        session = root_agent.create_session(user_id="test_user_456")
        session_id = session.id if hasattr(session, "id") else session["id"]

        # First, register a user
        register_msg = "I'm John Doe, VZ12345, john.doe@verizon.com"
        logger.info(f"Registering user: {register_msg}")

        for event in root_agent.stream_query(
            user_id="test_user_456",
            session_id=session_id,
            message=register_msg,
        ):
            logger.debug(f"Registration event: {event}")

        # Now try a GMeet query
        query_msg = "What GMeet agents are available?"
        logger.info(f"Querying: {query_msg}")

        response_parts = []
        for event in root_agent.stream_query(
            user_id="test_user_456",
            session_id=session_id,
            message=query_msg,
        ):
            logger.debug(f"Query event: {event}")

            if isinstance(event, dict):
                content = event.get("content", {})
                parts = content.get("parts", [])
                if parts is not None:
                    for part in parts:
                        if isinstance(part, dict) and "text" in part:
                            response_parts.append(part["text"])

        response = "\n".join(response_parts)
        logger.info(f"Query response: {response}")
        print("\n=== QUERY RESPONSE ===")
        print(response)

    except Exception as exc:
        logger.exception("Error during registration test: %s", exc)
        raise


if __name__ == "__main__":
    print("Starting EQES Agent Debugging...")
    print("Logs will be written to debug_agent.log")
    print("\n" + "=" * 80 + "\n")

    print("Test 1: Simple query")
    try:
        test_simple_query()
    except Exception:
        print("Test 1 FAILED - see debug_agent.log for details")

    print("\n" + "=" * 80 + "\n")

    print("Test 2: With user registration")
    try:
        test_with_registration()
    except Exception:
        print("Test 2 FAILED - see debug_agent.log for details")

    print("\n" + "=" * 80 + "\n")
    print("Debugging complete. Check debug_agent.log for full details.")

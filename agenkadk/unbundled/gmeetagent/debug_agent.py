"""Debug script to test the gmeetagent and see detailed errors."""
import logging
import sys

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("debug_gmeetagent.log"),
    ],
)

logger = logging.getLogger(__name__)

# Import the agent
from app.agent import root_agent
from app.response_validator import validate_event_parts, extract_text_from_event, sanitize_event


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
        event_count = 0

        for event in root_agent.stream_query(
            user_id="test_user_123",
            session_id=session.id if hasattr(session, "id") else session["id"],
            message=test_message,
        ):
            event_count += 1
            logger.info(f"Event {event_count}: type={type(event)}")

            # Validate event structure
            is_valid = validate_event_parts(event)
            logger.info(f"Event {event_count}: valid={is_valid}")

            if not is_valid:
                logger.warning(f"Event {event_count}: Invalid structure, sanitizing...")
                event = sanitize_event(event)

            # Extract text safely
            text = extract_text_from_event(event)
            if text:
                logger.info(f"Event {event_count}: extracted {len(text)} chars")
                response_parts.append(text)
            else:
                logger.debug(f"Event {event_count}: no text extracted")

        response = "\n".join(response_parts)
        logger.info(f"Total events: {event_count}")
        logger.info(f"Final response ({len(response)} chars): {response[:200]}...")
        print("\n=== RESPONSE ===")
        print(response)

    except Exception as exc:
        logger.exception("Error during test: %s", exc)
        raise


def test_with_eid():
    """Test with EID registration and score query."""
    logger.info("Starting test with EID registration...")

    try:
        session = root_agent.create_session(user_id="test_user_456")
        session_id = session.id if hasattr(session, "id") else session["id"]

        # First, register an EID
        register_msg = "My EID is VZ12345"
        logger.info(f"Registering EID: {register_msg}")

        event_count = 0
        for event in root_agent.stream_query(
            user_id="test_user_456",
            session_id=session_id,
            message=register_msg,
        ):
            event_count += 1
            logger.debug(f"Registration event {event_count}: {event}")

            # Validate event
            if not validate_event_parts(event):
                logger.warning(f"Registration event {event_count} invalid, sanitizing")
                event = sanitize_event(event)

        logger.info(f"Registration completed: {event_count} events")

        # Now try a score query
        query_msg = "Show my GMeet score for the last 5 days"
        logger.info(f"Querying: {query_msg}")

        response_parts = []
        event_count = 0

        for event in root_agent.stream_query(
            user_id="test_user_456",
            session_id=session_id,
            message=query_msg,
        ):
            event_count += 1
            logger.info(f"Query event {event_count}: type={type(event)}")

            # Validate and extract
            if not validate_event_parts(event):
                logger.warning(f"Query event {event_count} invalid, sanitizing")
                event = sanitize_event(event)

            text = extract_text_from_event(event)
            if text:
                response_parts.append(text)

        response = "\n".join(response_parts)
        logger.info(f"Query response ({len(response)} chars): {response[:200]}...")
        print("\n=== QUERY RESPONSE ===")
        print(response)

    except Exception as exc:
        logger.exception("Error during EID test: %s", exc)
        raise


def test_room_query():
    """Test room score query which often has large responses."""
    logger.info("Starting room query test...")

    try:
        session = root_agent.create_session(user_id="test_user_789")
        session_id = session.id if hasattr(session, "id") else session["id"]

        # Query room scores
        query_msg = "What GMeet scores for Chennai rooms?"
        logger.info(f"Querying: {query_msg}")

        response_parts = []
        event_count = 0

        for event in root_agent.stream_query(
            user_id="test_user_789",
            session_id=session_id,
            message=query_msg,
        ):
            event_count += 1
            logger.info(f"Room query event {event_count}: type={type(event)}")

            # Log event structure for debugging
            if isinstance(event, dict):
                logger.debug(f"Event {event_count} keys: {list(event.keys())}")
                if "content" in event:
                    content = event["content"]
                    logger.debug(f"Content type: {type(content)}")
                    if isinstance(content, dict):
                        logger.debug(f"Content keys: {list(content.keys())}")
                        parts = content.get("parts")
                        logger.debug(f"Parts type: {type(parts)}, value: {parts}")

            # Validate and extract
            if not validate_event_parts(event):
                logger.warning(f"Room event {event_count} invalid, sanitizing")
                event = sanitize_event(event)

            text = extract_text_from_event(event)
            if text:
                logger.info(f"Event {event_count}: extracted {len(text)} chars")
                response_parts.append(text)

        response = "\n".join(response_parts)
        logger.info(f"Total events: {event_count}")
        logger.info(f"Room response ({len(response)} chars): {response[:500]}...")
        print("\n=== ROOM RESPONSE ===")
        print(response[:1000])  # Print first 1000 chars
        if len(response) > 1000:
            print(f"\n... ({len(response) - 1000} more chars)")

    except Exception as exc:
        logger.exception("Error during room query test: %s", exc)
        raise


if __name__ == "__main__":
    print("Starting GMeet Agent Debugging...")
    print("Logs will be written to debug_gmeetagent.log")
    print("\n" + "=" * 80 + "\n")

    print("Test 1: Simple query")
    try:
        test_simple_query()
    except Exception:
        print("Test 1 FAILED - see debug_gmeetagent.log for details")

    print("\n" + "=" * 80 + "\n")

    print("Test 2: With EID registration and score query")
    try:
        test_with_eid()
    except Exception:
        print("Test 2 FAILED - see debug_gmeetagent.log for details")

    print("\n" + "=" * 80 + "\n")

    print("Test 3: Room score query (large response)")
    try:
        test_room_query()
    except Exception:
        print("Test 3 FAILED - see debug_gmeetagent.log for details")

    print("\n" + "=" * 80 + "\n")
    print("Debugging complete. Check debug_gmeetagent.log for full details.")

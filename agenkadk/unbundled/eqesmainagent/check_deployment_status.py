"""Check the deployment status of the eqesmainagent reasoning engine."""
import time
import vertexai
from vertexai import agent_engines

PROJECT_ID = "vz-it-np-voev-dev-voevdo-0"
REGION = "us-east4"
RESOURCE_NAME = "projects/1041813482705/locations/us-east4/reasoningEngines/7058204943345254400"

def check_status():
    """Check and wait for the reasoning engine to become ACTIVE."""
    vertexai.init(project=PROJECT_ID, location=REGION)

    print(f"Checking status of: {RESOURCE_NAME}")
    print("-" * 80)

    max_wait = 600  # 10 minutes
    check_interval = 10  # Check every 10 seconds
    elapsed = 0

    while elapsed < max_wait:
        try:
            engine = agent_engines.get(RESOURCE_NAME)

            # Try to get state attribute
            state = getattr(engine, 'state', 'UNKNOWN')
            display_name = getattr(engine, 'display_name', 'Unknown')

            print(f"[{elapsed}s] Agent: {display_name}")
            print(f"[{elapsed}s] State: {state}")

            if state == "ACTIVE" or state == 1:  # 1 is the enum value for ACTIVE
                print("\n✅ Agent is ACTIVE and ready for deployment!")
                return True
            elif state == "UPDATING" or state == 2:  # 2 is UPDATING
                print(f"[{elapsed}s] Still updating... waiting {check_interval}s")
            elif state == "FAILED" or state == 4:  # 4 is FAILED
                print("\n❌ Agent deployment FAILED!")
                return False
            else:
                print(f"[{elapsed}s] Unknown state: {state}")

        except Exception as exc:
            print(f"[{elapsed}s] Error checking status: {exc}")
            print(f"[{elapsed}s] Retrying in {check_interval}s...")

        time.sleep(check_interval)
        elapsed += check_interval

    print(f"\n⏱️ Timeout after {max_wait}s. Agent may still be updating.")
    return False


if __name__ == "__main__":
    success = check_status()

    if success:
        print("\n" + "=" * 80)
        print("You can now run the deployment:")
        print("  python deploy_to_agent_runtime.py")
        print("=" * 80)
    else:
        print("\n" + "=" * 80)
        print("Wait a bit longer or check the GCP console for errors:")
        print(f"  https://console.cloud.google.com/vertex-ai/agent-builder/engines?project={PROJECT_ID}")
        print("=" * 80)

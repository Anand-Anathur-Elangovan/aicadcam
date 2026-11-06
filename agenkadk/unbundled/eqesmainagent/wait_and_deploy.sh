#!/bin/bash
# Wait for the reasoning engine to become ACTIVE, then deploy

echo "Waiting for eqesmainagent to finish updating..."
echo "This may take 5-10 minutes."
echo ""

# Wait 2 minutes before first retry
echo "⏱️  Waiting 2 minutes before first retry..."
sleep 120

# Try deployment up to 5 times with 2-minute intervals
for i in {1..5}; do
    echo ""
    echo "========================================="
    echo "Deployment attempt $i of 5"
    echo "========================================="

    python3 deploy_to_agent_runtime.py

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Deployment successful!"
        exit 0
    fi

    if [ $i -lt 5 ]; then
        echo ""
        echo "⏱️  Deployment failed. Waiting 2 minutes before retry $((i+1))..."
        sleep 120
    fi
done

echo ""
echo "❌ Deployment failed after 5 attempts."
echo "The agent may still be updating. Check status at:"
echo "https://console.cloud.google.com/vertex-ai/agent-builder/engines?project=vz-it-np-voev-dev-voevdo-0"
exit 1

# Deployment Failed: Agent Currently Updating

## Error
```
Cannot update ReasoningEngine 7058204943345254400 as it is not in ACTIVE state. 
Current state: UPDATING
```

## What Happened
The agent is still processing a previous deployment. You cannot deploy again until it finishes and returns to the **ACTIVE** state.

## Solution Options

### Option 1: Automatic Wait & Retry (Recommended)
I created a script that will wait and retry deployment automatically:

```bash
cd /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent
./wait_and_deploy.sh
```

This script will:
- Wait 2 minutes
- Try deployment
- If it fails, wait 2 more minutes and retry (up to 5 attempts)
- Total max wait time: ~10 minutes

### Option 2: Manual Check & Deploy

**Step 1: Wait**
Wait 5-10 minutes for the current update to complete.

**Step 2: Check status in GCP Console**
```
https://console.cloud.google.com/vertex-ai/agent-builder/engines?project=vz-it-np-voev-dev-voevdo-0
```

Look for:
- **Agent Name:** eqes_orchestrator
- **State:** Should say "Active" (green checkmark)

**Step 3: Deploy again**
```bash
cd /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent
python3 deploy_to_agent_runtime.py
```

### Option 3: Check Status Programmatically

Run the status checker script:
```bash
cd /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent
python3 check_deployment_status.py
```

This will:
- Check the agent state every 10 seconds
- Show you when it becomes ACTIVE
- Timeout after 10 minutes

## Timeline

Typical deployment update times:
- **Small changes (code only):** 2-5 minutes
- **Medium changes (dependencies):** 5-10 minutes  
- **Large changes (major updates):** 10-15 minutes

## What NOT To Do

❌ **Don't run deployment again immediately** - This will fail with the same error

❌ **Don't delete and recreate** - You'll lose the existing agent configuration

❌ **Don't cancel the update** - Let it finish naturally

## Verification After Deployment

Once deployment succeeds, verify it worked:

```bash
# Check for errors in logs
gcloud logging read "resource.type=reasoning_engine \
  AND jsonPayload.message=~'eqes|NoneType'" \
  --limit 20 \
  --project vz-it-np-voev-dev-voevdo-0
```

Look for:
- ✅ No "NoneType object is not subscriptable" errors
- ✅ "Event has invalid parts structure, sanitizing..." (validation working)
- ✅ Successful tool executions

## If Update Fails

If the agent state becomes **FAILED** instead of **ACTIVE**:

1. **Check error logs:**
   ```bash
   gcloud logging read "resource.type=reasoning_engine \
     AND severity>=ERROR \
     AND timestamp>=2026-09-04T20:00:00Z" \
     --limit 50 \
     --project vz-it-np-voev-dev-voevdo-0
   ```

2. **Common causes:**
   - Import errors (missing modules)
   - Syntax errors in code
   - Dependency conflicts
   - Resource limits exceeded

3. **Fix and redeploy:**
   - Fix the issue in code
   - Wait for state to return to ACTIVE (or FAILED)
   - Deploy again

## Troubleshooting

### The wait script doesn't work
```bash
# Make sure it's executable
chmod +x /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent/wait_and_deploy.sh

# Run with bash explicitly
bash /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent/wait_and_deploy.sh
```

### Still failing after 10 minutes
The previous update may have failed. Check the console:
```
https://console.cloud.google.com/vertex-ai/agent-builder/engines?project=vz-it-np-voev-dev-voevdo-0
```

If state is **FAILED**, you can deploy again immediately.

### Need to cancel the update
You cannot cancel a reasoning engine update in progress. You must wait for it to finish (success or failure).

## Alternative: Deploy to a New Agent

If you need to deploy immediately and can't wait:

1. **Modify deploy script to create a new agent:**
   ```bash
   cd /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent
   # Edit deploy_to_agent_runtime.py
   # Comment out the AGENT_RESOURCE_NAME line to create new agent
   ```

2. **Deploy as new agent:**
   ```bash
   python3 deploy_to_agent_runtime.py
   ```

3. **Update orchestrator registry:**
   Update the resource_name in the calling agents to point to the new agent

**Warning:** This creates a duplicate agent. Delete the old one once confirmed working.

## Summary

**Recommended Action:**
```bash
cd /Users/ANATAN8/Desktop/project/agentadkhlc/eqesmainagent
./wait_and_deploy.sh
```

This will handle everything automatically!

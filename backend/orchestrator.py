"""Pipeline orchestrator and job state machine.

Drives the created → researching → briefing_ready → user_editing →
regenerating_section → approved → generating_documents → complete | failed
state transitions. Persists state to jobs/{job_id}/state.json.

Implemented in step 9 (state machine) and step 7 (Stage 1 agents).
"""
# TODO: implement in steps 7 and 9

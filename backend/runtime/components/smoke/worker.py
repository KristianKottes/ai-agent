"""Small first-party process used to verify runtime lifecycle handling."""

import json
import os
import time


component_id = os.environ.get("AI_BLOGGER_COMPONENT_ID", "runtime-smoke-test")
print(json.dumps({"status": "ready", "component_id": component_id}), flush=True)

while True:
    time.sleep(1)

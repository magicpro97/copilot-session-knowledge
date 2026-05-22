#!/usr/bin/env python3
# Fixture for testing that stdin is set to DEVNULL (closed) by the Rust proxy.
# If stdin is /dev/null, sys.stdin.read() returns "" immediately.
# If stdin is inherited (not DEVNULL), this script would block the test.
import json, sys
data = sys.stdin.read()
print(json.dumps({"stdin_was_closed": data == "", "stdin_len": len(data)}))
sys.exit(0)

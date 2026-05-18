# Session — declarative-phoenix implementation

**Date:** 2026-05-17T232330  
**Work:** Fixed granite4.1 bugs, assembled complete phoenix.py, ran devstral:24b concurrently  

---

## What this session did

Three prior sessions produced design fragments:
- `2026-05-17T231618` — deepseek-r1:14b designed the YAML schema + class scaffold (left stubs)
- `2026-05-17T232033` — granite4.1:8b implemented UFW parser + docker drift detector (had 2 bugs)
- This session — Claude fixed bugs, assembled `phoenix.py`; devstral:24b ran concurrently on `cmd_status`

---

## Bug fixes applied

### Bug 1 — UFW regex group order (granite4.1)

Pattern `r'\[(\d+)\] (\S+)/(\S+)'` has two problems:
1. `[ 1]` has a leading space — `\[(\d+)\]` fails to match
2. Groups captured as `(rule, port, proto)` but destructured as `rule_number, proto, port` — swapped

**Fix in phoenix.py:**
```python
# Before (broken):
match = re.match(r'\[(\d+)\] (\S+)/(\S+)', line)
rule_number, proto, port = match.groups()

# After:
match = re.match(r'\[\s*\d+\]\s+(\d+)/(\w+)\s+(\S+)', line)
port, proto, action = match.groups()  # also captures action directly from regex
```

### Bug 2 — Docker port parsing (granite4.1)

Original used walrus operator inside a dict comprehension `if` clause (SyntaxError) and assumed PortBindings is a string to regex. It's actually:
```json
{"3000/tcp": [{"HostIp": "", "HostPort": "3000"}]}
```

**Fix in phoenix.py:**
```python
port_bindings = data.get("HostConfig", {}).get("PortBindings", {}) or {}
ports: dict[str, str] = {}
for container_port, bindings in port_bindings.items():
    port_num = container_port.split("/")[0]
    if bindings:
        ports[port_num] = bindings[0].get("HostPort", "")
```

---

## Concurrent devstral:24b run

While phoenix.py was being written, `devstral:24b` was sent the `cmd_status` implementation task.

**Notable:** devstral's first run got stuck in `--wait` because `delegate.sh` hardcodes `KEEP_ALIVE_SECONDS=300` but the actual Ollama config is `OLLAMA_KEEP_ALIVE=60m` (3600s). The "ACTIVE" heuristic fired on granite4.1's warm keep-alive and waited indefinitely. Re-run without `--wait` worked immediately — Ollama queued it and swapped models fine.

**devstral output quality:**
- Got the structure right: helper functions, table print, colorize via ANSI codes, summary line
- Bug: systemd status comparison does `desired_state == actual_state` where desired is "running" but actual is "active" — always shows DRIFT
- UFW check uses `ufw status` without `numbered` and just checks if port appears as substring — simple but functional
- Already incorporated into phoenix.py in corrected form

---

## Files

| File | Description |
|---|---|
| `phoenix.py` | Complete implementation — UFW parser, docker drift, diff, status, apply |
| `worlock.yaml` | Example manifest with real worlock services |
| `devstral_status_verb.txt` | Raw devstral output for cmd_status |

---

## Usage

```bash
cd ~/Documents/claude_creations/2026-05-17T232330-declarative-phoenix/

# Check live state against manifest
python phoenix.py worlock.yaml status

# Show only drifted items
python phoenix.py worlock.yaml diff

# Apply changes (dry-run first)
python phoenix.py worlock.yaml apply --dry-run
python phoenix.py worlock.yaml apply
```

Requires: `pip install pyyaml`

---

## delegate.sh keep-alive bug (for future reference)

`delegate.sh` line: `KEEP_ALIVE_SECONDS=300`  
Actual Ollama config: `OLLAMA_KEEP_ALIVE=60m` (set in `/etc/systemd/system/ollama.service.d/override.conf`)

The `--wait` "ACTIVE" heuristic compares `expires_at` against `now + KEEP_ALIVE_SECONDS - ACTIVE_THRESHOLD`.  
With 300s threshold vs 3600s actual, any warm model looks like it's "actively generating."  
Fix: update `KEEP_ALIVE_SECONDS=3600` in delegate.sh, or just avoid `--wait` when OLLAMA_MAX_LOADED_MODELS=2.

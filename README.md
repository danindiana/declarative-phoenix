# declarative-phoenix

Desired-state manifest tool for Linux/systemd workstations. Define what should be running in a YAML file; `phoenix` tells you what's drifted and can reconcile it.

```
TYPE      NAME          DESIRED     ACTUAL      STATUS
──────────────────────────────────────────────────────
systemd   nginx         running     active      OK
systemd   ollama        running     inactive    DRIFT
docker    open-webui    running     exited      DRIFT
ufw       22222/tcp     ALLOW       —           OK
ufw       3000/tcp      ALLOW       —           OK
──────────────────────────────────────────────────────
3 ok  2 drift  0 missing
```

## Install

```bash
pip install pyyaml
```

## Usage

```bash
python phoenix.py <manifest.yaml> status       # show full state table
python phoenix.py <manifest.yaml> diff         # show only drifted items
python phoenix.py <manifest.yaml> apply        # reconcile live state to manifest
python phoenix.py <manifest.yaml> apply --dry-run
```

## What `apply` does

| Type | Drift | Action |
|---|---|---|
| systemd | not running | `systemctl start <name>` |
| systemd | not enabled | `systemctl enable <name>` |
| docker | wrong image | `docker pull <image>` + `docker restart <name>` |
| docker | wrong ports | prints recreation command (port changes require `docker rm`) |
| ufw | rule missing | `sudo ufw allow/deny <port>/<proto>` |

## Manifest schema

```yaml
systemd:
  - name: nginx           # systemctl service name
    state: running        # "running" (checks active) or "enabled" (checks active|inactive)
  - name: backup-timer
    state: enabled

docker:
  - name: open-webui
    image: ghcr.io/open-webui/open-webui:main
    ports:
      "3000": "3000"      # "container_port": "host_port"

ufw:
  - port: 22222
    protocol: tcp         # tcp | udp
    action: ALLOW
    from_ip: 192.168.1.0/24

env_vars:                 # informational only — not applied
  - name: OLLAMA_HOST
    value: "127.0.0.1"
```

See [`worlock.yaml`](worlock.yaml) for a real example.

## How systemd state is checked

`phoenix` calls `systemctl is-active <name>` with a 3-second timeout. If D-Bus is unavailable (common in restricted shell sessions), it falls back to reading `/sys/fs/cgroup/system.slice/<name>.service/pids.current`. Timers and services with no cgroup entry show `unknown`.

## UFW parsing

Handles all common `ufw status numbered` formats:

- `22222/tcp on enp7s0   ALLOW IN   Anywhere` (interface-bound)
- `3000                  ALLOW IN   192.168.1.0/24` (no protocol — assumed tcp)
- `443/tcp               ALLOW IN   Anywhere`

## Origin

Built across four local Ollama model sessions:
- `deepseek-r1:14b` — YAML schema and class scaffold
- `granite4.1:8b` — UFW parser and docker drift detector (stub implementations)
- `devstral:24b` — `cmd_status` implementation
- `gemma4:31b-it-q4_K_M` — `apply_docker_change` and `apply_ufw_change`

Bugs fixed and assembled by [Claude Code](https://claude.ai/code).

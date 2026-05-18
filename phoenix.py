#!/usr/bin/env python3
"""declarative-phoenix — desired-state manifest tool for worlock."""

import argparse
import json
import re
import subprocess
import sys
from typing import Any

import yaml


# ── live-state readers ────────────────────────────────────────────────────────

def get_live_ufw_rules() -> list[dict]:
    result = subprocess.run(
        ["sudo", "ufw", "status", "numbered"], capture_output=True, text=True
    )
    rules = []
    for line in result.stdout.splitlines():
        # Handles: "[ 1] 22222/tcp on enp7s0   ALLOW IN   Anywhere"
        #          "[ 2] 3000                   ALLOW IN   192.168.1.0/24"
        match = re.match(
            r"\[\s*\d+\]\s+(\d+)(?:/(\w+))?(?:\s+on\s+\S+)?\s+(ALLOW|DENY)", line
        )
        if match:
            port, proto, action = match.groups()
            from_ip = line.split()[-1]
            rules.append(
                {
                    "port": int(port),
                    "protocol": (proto or "tcp").lower(),
                    "action": action.upper(),
                    "from_ip": from_ip,
                }
            )
    return rules


def get_live_docker_containers() -> list[dict]:
    ids_result = subprocess.run(
        ["docker", "ps", "-q"], capture_output=True, text=True
    )
    containers = []
    for cid in ids_result.stdout.split():
        inspect = subprocess.run(
            ["docker", "inspect", cid, "--format", "{{json .}}"],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            continue
        data = json.loads(inspect.stdout)
        # PortBindings: {"3000/tcp": [{"HostIp": "", "HostPort": "3000"}], ...}
        port_bindings = data.get("HostConfig", {}).get("PortBindings", {}) or {}
        ports: dict[str, str] = {}
        for container_port, bindings in port_bindings.items():
            port_num = container_port.split("/")[0]
            if bindings:
                ports[port_num] = bindings[0].get("HostPort", "")
        containers.append(
            {
                "name": data["Name"].lstrip("/"),
                "image": data["Config"]["Image"],
                "ports": ports,
            }
        )
    return containers


def get_live_systemd_state(name: str) -> str:
    # systemctl needs D-Bus; fall back to cgroup if it times out
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3,
        )
        state = r.stdout.strip()
        if state:
            return state
    except subprocess.TimeoutExpired:
        pass

    cgroup = f"/sys/fs/cgroup/system.slice/{name}.service/pids.current"
    try:
        with open(cgroup) as f:
            return "active" if int(f.read().strip()) > 0 else "inactive"
    except (FileNotFoundError, ValueError):
        pass

    return "unknown"


# ── diff ──────────────────────────────────────────────────────────────────────

def check_docker_drift(
    desired: list[dict], live: list[dict]
) -> list[dict[str, Any]]:
    drift = []
    live_map = {c["name"]: c for c in live}

    for d in desired:
        name = d["name"]
        if name not in live_map:
            drift.append(
                {"type": "docker", "container": name, "field": "image", "desired": d["image"], "actual": None}
            )
            continue

        l = live_map[name]

        if d["image"] != l["image"]:
            drift.append(
                {
                    "type": "docker",
                    "container": name,
                    "field": "image",
                    "desired": d["image"],
                    "actual": l["image"],
                }
            )

        for manifest_port, manifest_host in d.get("ports", {}).items():
            actual_host = l["ports"].get(str(manifest_port))
            if actual_host != str(manifest_host):
                drift.append(
                    {
                        "type": "docker",
                        "container": name,
                        "field": "ports",
                        "desired": {manifest_port: manifest_host},
                        "actual": {manifest_port: actual_host},
                    }
                )

    return drift


def compute_diff(manifest: dict, live_systemd: dict, live_docker: list) -> list[dict]:
    changes = []

    for svc in manifest.get("systemd", []):
        actual = live_systemd.get(svc["name"], "unknown")
        desired = svc["state"]
        ok = (desired == "running" and actual == "active") or \
             (desired == "enabled" and actual in ("active", "inactive"))
        if not ok:
            changes.append(
                {"type": "systemd", "name": svc["name"],
                 "desired": desired, "actual": actual}
            )

    changes.extend(check_docker_drift(manifest.get("docker", []), live_docker))

    live_ufw = get_live_ufw_rules()
    live_ports = {(r["port"], r["protocol"]) for r in live_ufw}
    for rule in manifest.get("ufw", []):
        key = (rule["port"], rule["protocol"].lower())
        if key not in live_ports:
            changes.append(
                {"type": "ufw", "port": rule["port"],
                 "protocol": rule["protocol"], "desired": "present", "actual": "missing"}
            )

    return changes


# ── CLI verbs ─────────────────────────────────────────────────────────────────

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def cmd_diff(manifest: dict) -> None:
    live_systemd = {
        svc["name"]: get_live_systemd_state(svc["name"])
        for svc in manifest.get("systemd", [])
    }
    live_docker = get_live_docker_containers()
    changes = compute_diff(manifest, live_systemd, live_docker)

    if not changes:
        print(f"{GREEN}No drift — manifest matches live state.{RESET}")
        return

    for c in changes:
        print(f"~ {c}")


def cmd_status(manifest: dict) -> None:
    rows = []
    ok = drift = missing = 0

    for svc in manifest.get("systemd", []):
        actual = get_live_systemd_state(svc["name"])
        desired = svc["state"]
        in_sync = (desired == "running" and actual == "active") or \
                  (desired == "enabled" and actual in ("active", "inactive"))
        if in_sync:
            status, color = "OK", GREEN
            ok += 1
        elif actual in ("inactive", "failed"):
            status, color = "DRIFT", RED
            drift += 1
        else:
            status, color = "UNKNOWN", YELLOW
            missing += 1
        rows.append(("systemd", svc["name"], desired, actual, status, color))

    for ctr in manifest.get("docker", []):
        r = subprocess.run(
            ["docker", "inspect", ctr["name"], "--format", "{{.State.Status}}"],
            capture_output=True, text=True,
        )
        actual = r.stdout.strip() if r.returncode == 0 else "missing"
        desired = "running"
        if actual == "running":
            status, color = "OK", GREEN
            ok += 1
        elif actual == "missing":
            status, color = "MISSING", RED
            missing += 1
        else:
            status, color = "DRIFT", RED
            drift += 1
        rows.append(("docker", ctr["name"], desired, actual, status, color))

    live_ufw = get_live_ufw_rules()
    live_ports = {(r["port"], r["protocol"]) for r in live_ufw}
    for rule in manifest.get("ufw", []):
        key = (rule["port"], rule["protocol"].lower())
        if key in live_ports:
            status, color = "OK", GREEN
            ok += 1
        else:
            status, color = "MISSING", RED
            missing += 1
        rows.append(
            ("ufw", f"{rule['port']}/{rule['protocol']}", "ALLOW", "—", status, color)
        )

    # table
    hdr = f"{BOLD}{'TYPE':<10}{'NAME':<30}{'DESIRED':<12}{'ACTUAL':<12}STATUS{RESET}"
    print(hdr)
    print("─" * 74)
    for typ, name, desired, actual, status, color in rows:
        print(f"{typ:<10}{name:<30}{desired:<12}{actual:<12}{color}{status}{RESET}")
    print("─" * 74)
    print(f"{GREEN}{ok} ok{RESET}  {RED}{drift} drift  {missing} missing{RESET}")


def apply_docker_change(c: dict, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    if c["field"] == "image":
        for cmd in [["docker", "pull", c["desired"]],
                    ["docker", "restart", c["container"]]]:
            print(f"{prefix}{' '.join(cmd)}")
            if not dry_run:
                subprocess.run(cmd, check=False)
    elif c["field"] == "ports":
        port_args = " ".join(f"-p {k}:{v}" for k, v in c["desired"].items())
        # look up image from manifest is not available here — user must recreate
        print(f"{prefix}WARNING: port changes require container recreation.")
        print(f"{prefix}  docker stop {c['container']} && docker rm {c['container']}")
        print(f"{prefix}  docker run -d --name {c['container']} {port_args} <image>")


def apply_ufw_change(c: dict, dry_run: bool) -> None:
    action = "allow" if c["desired"] == "present" else "deny"
    cmd = ["sudo", "ufw", action, f"{c['port']}/{c['protocol']}"]
    print(f"{'[dry-run] ' if dry_run else ''}{' '.join(cmd)}")
    if not dry_run:
        subprocess.run(cmd, check=False)


def cmd_apply(manifest: dict, dry_run: bool = False) -> None:
    live_systemd = {
        svc["name"]: get_live_systemd_state(svc["name"])
        for svc in manifest.get("systemd", [])
    }
    live_docker = get_live_docker_containers()
    changes = compute_diff(manifest, live_systemd, live_docker)

    if not changes:
        print("Nothing to apply.")
        return

    for c in changes:
        if c.get("type") == "systemd":
            action = "start" if c["desired"] == "running" else "enable"
            cmd = ["systemctl", action, c["name"]]
            print(f"{'[dry-run] ' if dry_run else ''}systemctl {action} {c['name']}")
            if not dry_run:
                subprocess.run(cmd, check=False)
        elif c.get("type") == "docker":
            apply_docker_change(c, dry_run)
        elif c.get("type") == "ufw":
            apply_ufw_change(c, dry_run)
        else:
            print(f"{'[dry-run] ' if dry_run else ''}manual action needed: {c}")


# ── entry ─────────────────────────────────────────────────────────────────────

def load_manifest(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="declarative-phoenix manifest tool")
    parser.add_argument("manifest", help="path to YAML manifest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show live vs desired state table")
    sub.add_parser("diff",   help="show only items that drift from desired state")
    apply_p = sub.add_parser("apply", help="reconcile live state to manifest")
    apply_p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    manifest = load_manifest(args.manifest)

    if args.cmd == "status":
        cmd_status(manifest)
    elif args.cmd == "diff":
        cmd_diff(manifest)
    elif args.cmd == "apply":
        cmd_apply(manifest, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

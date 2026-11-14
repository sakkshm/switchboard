"""Reset only this checkout's switchboard Compose data, with explicit confirmation."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Delete switchboard project's named volumes and recreate the stack")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("Reset deletes this Compose project's graph, cache and MinIO volumes. Pass --confirm to perform it.")
    # Absolute compose file and explicit project prevent accidental deletion of
    # unrelated MinIO services or a project inherited from shell environment.
    compose = ["docker", "compose", "--project-name", "switchboard", "--file", str(ROOT / "compose.yaml")]
    subprocess.run([*compose, "down", "--volumes"], cwd=ROOT, check=True)
    subprocess.run([*compose, "up", "--detach", "--build"], cwd=ROOT, check=True)
    print("Switchboard volumes recreated. Run scripts/probe.py, then scripts/seed.py.")


if __name__ == "__main__":
    main()

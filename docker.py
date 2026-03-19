"""
Local Docker shortcuts — does NOT affect staging or production.

Usage:
    python docker.py dev    → build & start local containers
    python docker.py stop   → stop local containers
    python docker.py logs   → tail container logs
    python docker.py shell  → open shell inside raven_dev container
"""

import subprocess
import sys

COMPOSE = ["docker", "compose", "-f", "docker-compose.dev.yml"]

commands = {
    "dev":   COMPOSE + ["up", "--build"],
    "stop":  COMPOSE + ["down"],
    "logs":  COMPOSE + ["logs", "-f"],
    "shell": ["docker", "exec", "-it", "raven_dev", "/bin/bash"],
}

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg not in commands:
        print(__doc__)
        sys.exit(1)
    subprocess.run(commands[arg])

if __name__ == "__main__":
    main()

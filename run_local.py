"""Zero-friction local launcher for FinOps & Hybrid AI Resource Governance Gateway."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Ensure repo root is in python path
repo_root = Path(__file__).resolve().parent
sys.path.insert(0, str(repo_root))

from src.config import settings
from src.database import get_db_connection
from src.seed_finops_telemetry import seed_database


def print_banner() -> None:
    banner = f"""
================================================================================
⚡ FINOPS & HYBRID AI RESOURCE GOVERNANCE GATEWAY
================================================================================
  Target SLA: Sub-20ms Semantic Cache Hits (<$0.00005 COGS)
  Hybrid Routing: Local Ollama Llama 3 vs Frontier GPT-5/Claude
  FinOps Accounting: Multi-Tenant Real-Time COGS Attribution & Margin Guard (>75%)
  Security Controls: OWASP LLM01 Prompt Injection & LLM06 PII Edge Redaction

  🚀 Gateway API Server:     http://localhost:{settings.GATEWAY_PORT}
  📖 Interactive OpenAPI:    http://localhost:{settings.GATEWAY_PORT}/docs
  📊 Streamlit Cockpit:      http://localhost:{settings.STREAMLIT_PORT}
  🏥 Liveness Healthcheck:   http://localhost:{settings.GATEWAY_PORT}/healthz
================================================================================
"""
    print(banner)


def main() -> None:
    print_banner()

    # 1. Initialize DuckDB & seed multi-tenant telemetry if not already present
    print("[1/3] Initializing DuckDB FinOps repository and seeding initial multi-tenant telemetry...")
    get_db_connection()
    seed_database(force_reseed=False)
    print("      ✓ DuckDB initialized with 3,000 multi-tenant telemetry logs and cache entries.")

    python_exe = sys.executable

    # 2. Launch FastAPI Gateway
    print(f"[2/3] Starting FastAPI FinOps Gateway on port {settings.GATEWAY_PORT}...")
    gateway_cmd = [
        python_exe, "-m", "uvicorn", "src.api.main:app",
        "--host", settings.GATEWAY_HOST,
        "--port", str(settings.GATEWAY_PORT),
        "--log-level", settings.LOG_LEVEL.lower()
    ]
    gateway_process = subprocess.Popen(gateway_cmd, cwd=str(repo_root))

    # 3. Launch Streamlit UI
    print(f"[3/3] Starting Streamlit FinOps Executive Cockpit on port {settings.STREAMLIT_PORT}...")
    streamlit_cmd = [
        python_exe, "-m", "streamlit", "run", "src/ui/dashboard.py",
        "--server.port", str(settings.STREAMLIT_PORT),
        "--server.address", settings.GATEWAY_HOST,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    ]
    streamlit_process = subprocess.Popen(streamlit_cmd, cwd=str(repo_root))

    print("\n✓ Both services are running concurrently! Press Ctrl+C to terminate.")

    def handle_exit(signum, frame):
        print("\nGracefully terminating FinOps Gateway processes...")
        try:
            gateway_process.terminate()
            streamlit_process.terminate()
            gateway_process.wait(timeout=5)
            streamlit_process.wait(timeout=5)
        except Exception:
            gateway_process.kill()
            streamlit_process.kill()
        print("Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Keep alive
    try:
        while True:
            time.sleep(1)
            # Check if any child process crashed
            if gateway_process.poll() is not None:
                print(f"Gateway process exited with code {gateway_process.returncode}")
                break
            if streamlit_process.poll() is not None:
                print(f"Streamlit process exited with code {streamlit_process.returncode}")
                break
    except KeyboardInterrupt:
        handle_exit(None, None)


if __name__ == "__main__":
    main()

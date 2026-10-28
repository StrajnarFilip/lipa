import argparse
import uvicorn

parser = argparse.ArgumentParser(description="Start the local Slovene Codex voice harness")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
uvicorn.run("voice_harness.app:app", host="127.0.0.1", port=args.port, log_level="info", access_log=False)

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def run_script(module_name: str):
    script_path = BASE_DIR / "scripts" / f"{module_name}.py"
    if not script_path.exists():
        raise FileNotFoundError(script_path)
    result = os.system(f"python3 {script_path}")
    if result != 0:
        raise RuntimeError(f"{module_name} exited with status {result}")


def main():
    print("Running vocabulary index parser...")
    run_script("vocab_index_parser")
    print("Running sentence parser...")
    run_script("sentence_parser")
    print("Running question generator...")
    run_script("create_questions")
    print("Pipeline completed")


if __name__ == "__main__":
    main()

import json
import os
import sys
from pathlib import Path
from app.core.config.characters import CHARACTER_SCRIPTS_DIR


def run_script(module_name: str):
    script_path = CHARACTER_SCRIPTS_DIR / f"{module_name}.py"
    if not script_path.exists():
        raise FileNotFoundError(script_path)
    result = os.system(f"python3 {script_path}")
    if result != 0:
        raise RuntimeError(f"{module_name} exited with status {result}")

def main():
    print("Populating ids")
    run_script("populate_ids")
    print("Populating confusibles")
    run_script("populate_confusibles")
    print("Populating radicals")
    run_script("populate_radicals")
    print("Pipeline completed")

if __name__ == "__main__":
    main()
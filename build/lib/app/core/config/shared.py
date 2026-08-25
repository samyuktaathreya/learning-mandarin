from pathlib import Path
from dotenv import load_dotenv

# Base Directories
CONFIG_DIR = Path(__file__).resolve().parent              # app/core/config
CORE_DIR = CONFIG_DIR.parent                              # app/core
APP_DIR = CORE_DIR.parent                                 # app
ROOT_DIR = APP_DIR.parent                                 # learning-mandarin (Repo Root)
BASE_DIR = ROOT_DIR  # ... sometimes i forget whether it's called BASE_DIR or ROOT_DIR...

# Paths
ENV_FILE = APP_DIR / ".env"
PIPELINE_DIR = ROOT_DIR / "data_pipelines"

# Load environment variables automatically when this config module is imported
load_dotenv(dotenv_path=ENV_FILE)
from pathlib import Path

# Base Directories
# This file lives in: app/core/config/shared.py
CONFIG_DIR = Path(__file__).resolve().parent              # app/core/config
CORE_DIR = CONFIG_DIR.parent                              # app/core
APP_DIR = CORE_DIR.parent                                 # app
ROOT_DIR = APP_DIR.parent                                 # learning-mandarin (Repo Root)
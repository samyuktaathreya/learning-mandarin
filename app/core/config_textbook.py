# app/core/config.py (minimal, just base paths)
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent          # app/core
ROOT_DIR = CORE_DIR.parent.parent                   # learning-mandarin/
DATA_PIPELINES_DIR = ROOT_DIR / "data_pipelines"    # data_pipelines/
DATA_DIR = ROOT_DIR / "data"                        # data/

def get_feature_paths(feature_name: str):
    """Helper to generate consistent paths for any feature"""
    pipeline_dir = DATA_PIPELINES_DIR / feature_name
    data_output_dir = DATA_DIR / feature_name
    data_output_dir.mkdir(parents=True, exist_ok=True)
    
    return {
        "pipeline_dir": pipeline_dir,
        "raw_dir": pipeline_dir / "data" / "raw",
        "clean_dir": pipeline_dir / "data" / "clean",
        "output_dir": data_output_dir,
    }
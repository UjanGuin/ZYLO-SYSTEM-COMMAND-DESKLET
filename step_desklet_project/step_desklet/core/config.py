import json
import os
import uuid
from typing import Dict, List, Any

CONFIG_DIR = os.path.expanduser("~/.local/share/step_desklet")
CONFIG_FILE = os.path.join(CONFIG_DIR, "desklets.json")

DEFAULT_CONFIG = {
    "instances": [
        {
            "id": str(uuid.uuid4()),
            "x": 100,
            "y": 100,
            "w": 320,
            "h": 110,
            "is_locked": False,
            "user_sized": False,
            "api_key": "",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "stepfun-ai/step-3.5-flash",
            "working_dir": "/",
            "sudo_password": "././././",
        }
    ]
}


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_configs() -> Dict[str, List[Dict[str, Any]]]:
    _ensure_config_dir()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("instances"), list):
                return data
        except Exception:
            pass

    save_configs(DEFAULT_CONFIG)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_configs(config: Dict[str, Any]):
    _ensure_config_dir()
    temp_file = f"{CONFIG_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, CONFIG_FILE)


def update_instance(instance_id: str, data: Dict[str, Any]):
    configs = load_configs()
    for i, inst in enumerate(configs["instances"]):
        if inst.get("id") == instance_id:
            configs["instances"][i].update(data)
            break
    save_configs(configs)


def add_instance() -> Dict[str, Any]:
    configs = load_configs()
    new_inst = {
        "id": str(uuid.uuid4()),
        "x": 150,
        "y": 150,
        "w": 320,
        "h": 110,
        "is_locked": False,
        "user_sized": False,
        "api_key": "",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "stepfun-ai/step-3.5-flash",
        "working_dir": "/",
        "sudo_password": "././././",
    }
    configs["instances"].append(new_inst)
    save_configs(configs)
    return new_inst


def remove_instance(instance_id: str):
    configs = load_configs()
    configs["instances"] = [inst for inst in configs["instances"] if inst.get("id") != instance_id]
    save_configs(configs)


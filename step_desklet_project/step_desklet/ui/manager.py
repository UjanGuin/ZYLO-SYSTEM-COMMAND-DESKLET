from PyQt6.QtCore import QObject
from .desklet import StepDesklet
from ..core.config import load_configs, add_instance, remove_instance

class DeskletManager(QObject):
    def __init__(self):
        super().__init__()
        self.desklets = {} # instance_id -> StepDesklet object

    def start(self):
        configs = load_configs()
        for inst_config in configs.get("instances", []):
            self.spawn_desklet(inst_config)

    def spawn_desklet(self, config):
        instance_id = config["id"]
        desklet = StepDesklet(instance_id, config, self)
        desklet.show()
        self.desklets[instance_id] = desklet

    def create_desklet(self):
        new_config = add_instance()
        self.spawn_desklet(new_config)

    def remove_desklet(self, instance_id):
        if instance_id in self.desklets:
            self.desklets[instance_id].close()
            del self.desklets[instance_id]
            remove_instance(instance_id)


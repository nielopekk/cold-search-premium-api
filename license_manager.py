# license_manager.py
import json
from pathlib import Path

LICENSE_FILE = Path("licenses.json")

class LicenseManager:
    def __init__(self):
        if not LICENSE_FILE.exists():
            LICENSE_FILE.write_text(json.dumps({}, indent=4))
        self.licenses = json.loads(LICENSE_FILE.read_text())

    def save(self):
        LICENSE_FILE.write_text(json.dumps(self.licenses, indent=4))

    def generate_key(self):
        import uuid
        key = str(uuid.uuid4()).replace("-", "").upper()
        self.licenses[key] = {
            "ip": None,
            "active": True
        }
        self.save()
        return key

    def revoke_key(self, key):
        if key in self.licenses:
            self.licenses[key]["active"] = False
            self.save()
            return True
        return False

    def validate_license(self, key, ip):
        if key not in self.licenses:
            return {"success": False, "message": "Nieprawidłowy klucz"}

        data = self.licenses[key]
        if not data["active"]:
            return {"success": False, "message": "Klucz zablokowany"}

        if data["ip"] is None:
            data["ip"] = ip
            self.save()
            return {"success": True, "message": "Klucz aktywowany"}

        if data["ip"] != ip:
            return {"success": False, "message": "Klucz przypisany do innego adresu IP"}

        return {"success": True, "message": "Dostęp przyznany"}
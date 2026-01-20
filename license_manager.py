# license_manager.py
import json
from datetime import datetime, timedelta
from pathlib import Path

LICENSE_FILE = Path("licenses.json")


class LicenseManager:
    def __init__(self):
        if not LICENSE_FILE.exists():
            LICENSE_FILE.write_text(json.dumps({}, indent=4))
        
        try:
            data = json.loads(LICENSE_FILE.read_text())
            if not isinstance(data, dict):
                raise ValueError("Zawartość licenses.json nie jest słownikiem")
            self.licenses = data
        except (json.JSONDecodeError, ValueError, OSError):
            # Jeśli plik jest uszkodzony — zresetuj do pustego słownika
            self.licenses = {}
            self.save()

    def save(self):
        """Zapisuje licencje do pliku."""
        LICENSE_FILE.write_text(json.dumps(self.licenses, indent=4))

    def reset_all_licenses(self):
        """Resetuje wszystkie licencje (np. po zmianie IP policy)."""
        self.licenses = {}
        self.save()
        return True

    def generate_key(self, valid_days=None):
        """Generuje nowy klucz licencyjny."""
        import uuid
        key = str(uuid.uuid4()).replace("-", "").upper()
        license_data = {
            "ip": None,
            "active": True,
            "activated": None,
            "expiry": None
        }
        if valid_days is not None and valid_days > 0:
            expiry_dt = datetime.utcnow() + timedelta(days=valid_days)
            license_data["expiry"] = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.licenses[key] = license_data
        self.save()
        return key

    def revoke_key(self, key):
        """Blokuje klucz licencyjny."""
        if key in self.licenses:
            self.licenses[key]["active"] = False
            self.save()
            return True
        return False

    def validate_license(self, key, ip):
        """Waliduje klucz dla danego adresu IP."""
        if key not in self.licenses:
            return {"success": False, "message": "Nieprawidłowy klucz"}

        data = self.licenses[key]

        if not data["active"]:
            return {"success": False, "message": "Klucz został zablokowany"}

        if data.get("expiry"):
            try:
                expiry = datetime.fromisoformat(data["expiry"].replace("Z", "+00:00"))
                if datetime.utcnow() > expiry:
                    return {"success": False, "message": "Klucz wygasł"}
            except ValueError:
                pass  # Ignoruj błędne daty

        if data["ip"] is None:
            data["ip"] = ip
            data["activated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save()
            msg = "Klucz aktywowany"
            if data["expiry"]:
                msg += f" — ważny do {data['expiry'][:10]}"
            return {"success": True, "message": msg}

        if data["ip"] != ip:
            return {"success": False, "message": "Klucz przypisany do innego adresu IP"}

        msg = "Dostęp przyznany"
        if data["expiry"]:
            msg += f" (wygasa {data['expiry'][:10]})"
        return {"success": True, "message": msg}

# license_manager.py
import json
from datetime import datetime, timedelta
from pathlib import Path

LICENSE_FILE = Path("licenses.json")


class LicenseManager:
    def __init__(self):
        if not LICENSE_FILE.exists():
            LICENSE_FILE.write_text(json.dumps({}, indent=4))
        self.licenses = json.loads(LICENSE_FILE.read_text())

    def save(self):
        """Zapisuje aktualny stan licencji do pliku."""
        LICENSE_FILE.write_text(json.dumps(self.licenses, indent=4))

    def generate_key(self, valid_days=None):
        """
        Generuje nowy klucz licencyjny.
        
        :param valid_days: Liczba dni ważności (None = bezterminowy)
        :return: Wygenerowany klucz (string)
        """
        import uuid
        key = str(uuid.uuid4()).replace("-", "").upper()

        license_data = {
            "ip": None,
            "active": True,
            "activated": None,
            "expiry": None  # ISO 8601 format: "2025-12-31T23:59:59Z"
        }

        if valid_days is not None and valid_days > 0:
            expiry_dt = datetime.utcnow() + timedelta(days=valid_days)
            license_data["expiry"] = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        self.licenses[key] = license_data
        self.save()
        return key

    def revoke_key(self, key):
        """
        Blokuje klucz licencyjny.
        
        :param key: Klucz do zablokowania
        :return: True jeśli klucz istniał, False w przeciwnym razie
        """
        if key in self.licenses:
            self.licenses[key]["active"] = False
            self.save()
            return True
        return False

    def validate_license(self, key, ip):
        """
        Waliduje klucz licencyjny dla danego adresu IP.
        
        :param key: Klucz licencyjny
        :param ip: Adres IP klienta
        :return: Słownik z wynikiem walidacji
        """
        if key not in self.licenses:
            return {"success": False, "message": "Nieprawidłowy klucz"}

        data = self.licenses[key]

        # Sprawdź, czy klucz nie jest zablokowany
        if not data["active"]:
            return {"success": False, "message": "Klucz został zablokowany"}

        # Sprawdź ważność czasową
        if data.get("expiry"):
            try:
                expiry = datetime.fromisoformat(data["expiry"].replace("Z", "+00:00"))
                if datetime.utcnow() > expiry:
                    return {"success": False, "message": "Klucz wygasł"}
            except ValueError:
                pass  # Jeśli data jest niepoprawna, traktuj jako bezterminowy

        # Jeśli to pierwsze użycie — aktywuj na tym IP
        if data["ip"] is None:
            data["ip"] = ip
            data["activated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save()
            message = "Klucz aktywowany"
            if data["expiry"]:
                message += f" — ważny do {data['expiry'][:10]}"
            return {"success": True, "message": message}

        # Sprawdź, czy IP się zgadza
        if data["ip"] != ip:
            return {"success": False, "message": "Klucz przypisany do innego adresu IP"}

        # Wszystko OK
        message = "Dostęp przyznany"
        if data["expiry"]:
            message += f" (wygasa {data['expiry'][:10]})"
        return {"success": True, "message": message}

"""Wspólny interfejs backendów systemowych + odczyt SMART przez smartctl."""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import Device, Health
from ..util import run, run_json, which


class BackendError(RuntimeError):
    pass


class Backend:
    name = "base"
    #: narzędzia systemowe używane przez backend (do trybu diagnostycznego)
    tools: List[str] = []

    # ---- do nadpisania
    def list_devices(self, include_virtual: bool = False) -> List[Device]:
        raise NotImplementedError

    def raw_path(self, device: Device) -> str:
        """Ścieżka, z której czytamy surowe dane nośnika."""
        return device.path

    def unmount(self, device: Device) -> List[str]:
        raise BackendError("Odmontowanie nie jest obsługiwane na tym systemie")

    def power_off(self, device: Device) -> str:
        raise BackendError("Przygotowanie do odłączenia nie jest obsługiwane na tym systemie")

    def system_health(self, device: Device) -> Optional[Health]:
        """Status zdrowia z narzędzi systemowych (gdy brak smartctl)."""
        return None

    # ---- wspólne
    def tool_status(self) -> Dict[str, Optional[str]]:
        return {t: which(t) for t in self.tools + ["smartctl"]}

    def read_health(self, device: Device) -> Health:
        h = smartctl_health(self.raw_path(device) if self.smart_uses_raw else device.path)
        if h.status is None:
            fallback = self.system_health(device)
            if fallback is not None:
                fallback.note = "; ".join(x for x in (fallback.note, h.note) if x) or None
                return fallback
        return h

    smart_uses_raw = False


def smartctl_health(path: str) -> Health:
    """Odczyt SMART przez smartctl (JSON). Nie zgaduje: brak danych -> pola None."""
    if not which("smartctl"):
        return Health(source="smartctl", note="smartctl nie jest zainstalowany (pakiet smartmontools)")
    data = None
    for extra in ([], ["-d", "sat"]):
        data = run_json(["smartctl", "-a", "-j", *extra, path], timeout=60, accept_nonzero=True)
        if data and "smart_status" in data:
            break
    if not data:
        return Health(source="smartctl", note="smartctl nie zwrócił danych")
    if "smart_status" not in data:
        msgs = "; ".join(m.get("string", "") for m in data.get("smartctl", {}).get("messages", []))
        return Health(source="smartctl", note=msgs or "urządzenie nie udostępnia SMART")

    passed = data["smart_status"].get("passed")
    h = Health(status=None if passed is None else ("PASSED" if passed else "FAILED"), source="smartctl")
    h.temperature_c = (data.get("temperature") or {}).get("current")
    h.power_on_hours = (data.get("power_on_time") or {}).get("hours")
    errors = None
    nvme = data.get("nvme_smart_health_information_log")
    if nvme:
        errors = nvme.get("media_errors")
        h.details["percentage_used"] = nvme.get("percentage_used")
        h.details["available_spare"] = nvme.get("available_spare")
        h.details["critical_warning"] = nvme.get("critical_warning")
        if h.temperature_c is None:
            h.temperature_c = nvme.get("temperature")
        if h.power_on_hours is None:
            h.power_on_hours = nvme.get("power_on_hours")
    else:
        log = (data.get("ata_smart_error_log") or {}).get("summary") or {}
        errors = log.get("count")
        for attr in (data.get("ata_smart_attributes") or {}).get("table", []):
            if attr.get("id") in (5, 197, 198):
                h.details[attr.get("name", str(attr["id"]))] = (attr.get("raw") or {}).get("value")
    h.error_count = errors
    return h

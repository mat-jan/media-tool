"""Wyjątki aplikacji."""


class MediaToolError(Exception):
    """Błąd, który pokazujemy użytkownikowi bez śladu stosu."""


class Cancelled(MediaToolError):
    pass


class SafetyError(MediaToolError):
    """Operacja zablokowana przez zabezpieczenia."""


class DeviceChanged(SafetyError):
    """Dane urządzenia zmieniły się między wykryciem a operacją."""


class RawAccessError(MediaToolError):
    """Brak dostępu do surowego urządzenia (zwykle: uprawnienia)."""


class ImagingError(MediaToolError):
    pass


class DeviceNotFound(MediaToolError):
    pass

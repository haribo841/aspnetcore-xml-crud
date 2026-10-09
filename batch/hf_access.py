"""Small, explicit access check and actionable messages without secret logging."""
from __future__ import annotations

import re

MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_URL = "https://huggingface.co/" + MODEL_ID
TOKENS_URL = "https://huggingface.co/settings/tokens"


def redact(message, token=""):
    if token:
        message = str(message).replace(token, "[token ukryty]")
    return re.sub(r"hf_\w+", "[token ukryty]", str(message), flags=re.ASCII)


def access_error(exc, token=""):
    """Return guidance instead of an HTTP traceback or a signed file URL."""
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
    import httpx
    if isinstance(exc, GatedRepoError):
        return {"ok": False, "code": "access_denied", "message":
                "Token nie ma dostępu do Community-1. Otwórz stronę modelu w kroku 1, "
                "zaloguj się na to samo konto i zaakceptuj warunki. Jeśli zgoda jest już przyznana, "
                "sprawdź uprawnienie tokenu do odczytu repozytoriów z ograniczonym dostępem."}
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, HfHubHTTPError) or status:
        if status == 401:
            return {"ok": False, "code": "invalid_token", "message":
                    "Hugging Face odrzucił token. Wklej cały token zaczynający się od hf_, "
                    "utworzony na koncie z dostępem do modelu. Hasło do konta nie jest tokenem."}
        if status in (403, 404):
            return {"ok": False, "code": "access_denied", "message":
                    "Nie można odczytać modelu. Sprawdź zgodę na stronie Community-1 oraz "
                    "uprawnienia odczytu tokenu. Konto w przeglądarce i konto tokenu muszą być takie same."}
        if status == 429:
            return {"ok": False, "code": "rate_limit", "message":
                    "Hugging Face ograniczył liczbę zapytań. Odczekaj chwilę i kliknij Sprawdź dostęp ponownie."}
        if status and status >= 500:
            return {"ok": False, "code": "network", "message":
                    "Hugging Face jest chwilowo niedostępny. Spróbuj ponownie za kilka minut."}
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError)):
        return {"ok": False, "code": "network", "message":
                "Nie udało się połączyć z Hugging Face. Sprawdź internet i spróbuj ponownie. "
                "Nie trzeba tworzyć nowego tokenu z powodu tego błędu."}
    return {"ok": False, "code": "error", "message":
            "Nie udało się zakończyć operacji: " + redact(str(exc), token)[:800]}


def check_access(token, revision):
    from huggingface_hub import HfApi, get_hf_file_metadata, hf_hub_url
    token = token.strip()
    if not re.fullmatch(r"hf_[A-Za-z0-9]+", token):
        return {"ok": False, "code": "invalid_token", "message":
                "Wklej token Hugging Face zaczynający się od hf_. Skopiuj jego wartość, "
                "a nie nazwę, adres strony ani hasło."}
    try:
        # No login(), credential store, model download or browser-cookie access.
        account = HfApi().whoami(token=token)
        get_hf_file_metadata(hf_hub_url(MODEL_ID, "config.yaml", revision=revision),
                             token=token, timeout=15, retry_on_errors=False)
        return {"ok": True, "code": "ready", "account": account.get("name", ""),
                "message": "Dostęp do Community-1 potwierdzony. Możesz pobrać model."}
    except Exception as exc:
        return access_error(exc, token)

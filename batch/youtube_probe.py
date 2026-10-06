"""A single, explicit access test, without downloads or queue state changes."""
from __future__ import annotations

import json
import sys

from .importers import video_id
from .media import classify_error, youtube_options, wait_before_youtube
from .youtube_access import SessionError, error_message, validate_preferences, youtube_client


def check_access(url, config, access):
    identifier = video_id(url)
    if not identifier:
        return {"ok": False, "code": "input", "message": "Podaj link do jednego filmu YouTube."}
    try:
        access = validate_preferences(access)
        options = youtube_options(config, access)
        wait_before_youtube(access, lambda *_: None)
        with youtube_client(options, access) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=" + identifier, download=False)
            if info.get("live_status") in {"is_live", "post_live", "is_upcoming"}:
                return {"ok": False, "code": "live", "message": "To transmisja trwająca, zaplanowana lub jeszcze przetwarzana. Wybierz ukończony film."}
            if not info.get("formats") and not info.get("url"):
                return {"ok": False, "code": "format", "message": "YouTube nie zwrócił dostępnego strumienia audio."}
            return {"ok": True, "code": "ready", "message":
                    "YouTube udostępnił informacje i formaty tego filmu. Test nie pobiera audio; samo pobranie może jeszcze zostać odrzucone. "
                    "Jeśli chcesz kontynuować kolejkę, zamknij to okno i naciśnij Start/Wznów."}
    except (SessionError, ValueError) as exc:
        return {"ok": False, "code": "session", "message": str(exc) if isinstance(exc, SessionError) else "Sprawdź ustawienia sesji i odstępów YouTube."}
    except Exception as exc:
        return {"ok": False, "code": classify_error(str(exc)), "message": error_message(exc)}


def main():
    try:
        request = json.loads(sys.stdin.readline())
        result = check_access(request["url"], request["config"], request["access"])
    except Exception:
        result = {"ok": False, "code": "error", "message": "Nie udało się uruchomić testu YouTube. Sprawdź lokalne ustawienia."}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

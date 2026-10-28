"""Один реальный пробный анализ без создания тренировочной сессии в базе."""
from datetime import datetime, timezone
import time
from .engine import new_session
from .gigachat import ProviderError


def check_connection(provider, bundle: dict) -> dict:
    started = time.perf_counter()
    models = provider.available_models()
    if not any(name == provider.model or name.startswith(provider.model + ":") for name in models):
        raise ProviderError("Выбранная модель отсутствует в списке доступных. Проверьте GIGACHAT_MODEL по кабинету и документации.")
    text = "Здравствуйте, Алексей. Меня зовут Дмитрий, я представитель ВТБ. Звоню по поводу назначенной на сегодня встречи для получения карты."
    analysis = provider.analyze(new_session(bundle), text)
    actions = sorted({item["code"] for item in analysis["actions"]})
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if "introduce" in actions else "needs_review",
        "authorization": "ok", "selected_model_available": True,
        "model": provider.model, "response_contract": "ok",
        "probe_actions": actions, "generation_requests": 1,
        "usage": dict(provider.metrics.get("usage", {})),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "notice": "Одна вымышленная реплика проверяет подключение и формат. Это не оценка качества модели на всём сценарии.",
    }

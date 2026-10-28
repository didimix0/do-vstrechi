"""Локальный запуск без установки пакета и сторонних зависимостей."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from do_vstrechi.cli import main
from do_vstrechi.contracts import ContractError
from do_vstrechi.gigachat import ProviderError

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProviderError, ContractError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print("Запуск остановлен:", str(exc))
        raise SystemExit(1)


"""Выбор относящихся к реплике предложений из закреплённой копии материалов."""
from copy import deepcopy
import re

TOPICS = {
    "cashback_snapshot": r"к[еэ]шб[еэ]к|категори|театр|кино|кафе|ресторан|супермаркет|аптек|ремонт|tasty|wildberries|вайлдбер|почт[аы]|путешеств",
    "grow_with_vtb": r"расти|раст[её]м|серебр|золот|уровн|уровень|магнит|мобайл|интеллект|комисси.{0,20}ценн",
    "family_and_close": r"семейн|семь[яеию]|близк|групп|родствен",
    "partner_offers": r"яндекс|маркет|путешеств|отел[ьяе]|скидк.{0,15}партн",
    "payment_tools": r"стикер|смартфон|vtbpay|втбп[еэ]й|android|андроид|бесконтакт|оплат.{0,15}телефон",
    "VTB_Plus": r"подписк|втб\s*плюс|vtb\s*plus|wink|винк|яндекс|такси|лавк|надбавк|накопитель|обмен.{0,15}валют|перевод.{0,15}(стран|границ)",
    "banking_tariffs": r"неактив|не\s*польз|не\s*использ|сч[её]т|остат[ок]|180|стикер|снят|сним|налич|банкомат|комисси",
    "cashback_rules": r"к[еэ]шб[еэ]к|категори|\bmcc\b|\bмсс\b|начисл|выплат|возврат|годов.{0,15}лимит",
    "new_client_offer": r"нов\w*\s+клиент|приветств|пробн|перв\w*\s+месяц|2\s*(?:%|процент)|дв[ау]\w*\s+процент",
    "pensioner_offer": r"пенси",
    "extra_cashback_mechanics": r"выходн|пятниц|суббот|воскрес|категори.{0,30}остат|остат.{0,30}категор|восемь.{0,15}категори|8\s+катег",
}


def select_product_context(product: dict, employee_text: str, history: list) -> dict:
    """Без новых сетевых запросов; старые сессии сохраняют закреплённые предложения."""
    result = deepcopy({key: value for key, value in product.items() if key != "conditional_offers"})
    # Несколько предыдущих реплик сохраняют тему уточняющего вопроса.
    text = "\n".join([str(turn.get("text", "")) for turn in history[-6:]] + [employee_text])
    selected = {key: deepcopy(value) for key, value in product.get("conditional_offers", {}).items()
                if key in TOPICS and re.search(TOPICS[key], text, re.I)}
    if selected:
        result["conditional_offers"] = selected
        result["conditional_offers_notice"] = (
            "Предложения имеют отдельные условия применения. Дата получения материалов не устанавливает "
            "дату действия. Неизвестный месяц скриншота и неизвестное право клиента нельзя подменять текущими."
        )
    return result

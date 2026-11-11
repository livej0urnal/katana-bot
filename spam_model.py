import math
import re
from collections import Counter


# Synthetic bootstrap examples. Replace or expand with reviewed data before tuning thresholds.
EXAMPLES = {
    "spam": (
        "Забирай бонус срочно переходи по ссылке", "Пассивный доход гарантия заработка пиши в личку",
        "Раздача денег каждому приз бесплатный нажми ссылку", "Скидка акция только сегодня купить дешево",
        "Вступай в канал подписка розыгрыш бонус", "Криптовалюта инвестируй удвоим депозит",
        "Claim your free reward click now", "Guaranteed profit invest crypto message me",
        "Earn easy money work from home limited offer", "Join our channel win prize free bonus",
        "Cheap deals buy now promo discount", "Your wallet is suspended verify password immediately",
    ),
    "normal": (
        "Всем привет кто знает как настроить приложение", "Спасибо твой ответ помог решить проблему",
        "Завтра обсуждаем план в группе", "Можно уточнить правила чата пожалуйста",
        "Пришлите ссылку на документацию", "Купил билет на поезд вчера скидка была небольшая",
        "Hi can someone explain how the app works", "Thanks for helping me fix the problem",
        "Meeting tomorrow please share your notes", "Is this the correct link to the documentation",
        "We can discuss the group rules next week", "I forgot my password and changed it yesterday",
    ),
}


def tokens(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text.casefold())[:150]


COUNTS = {label: Counter(tokens(" ".join(examples))) for label, examples in EXAMPLES.items()}
VOCABULARY = set().union(*COUNTS.values())
TOTALS = {label: sum(counts.values()) + len(VOCABULARY) for label, counts in COUNTS.items()}


def spam_probability(text: str) -> float:
    words = set(tokens(text)) & VOCABULARY
    log_odds = sum(
        math.log((COUNTS["spam"][word] + 1) / TOTALS["spam"])
        - math.log((COUNTS["normal"][word] + 1) / TOTALS["normal"])
        for word in words
    )
    return 1 / (1 + math.exp(-log_odds))

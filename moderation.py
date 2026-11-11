import re
import unicodedata
from collections.abc import Iterable


WORD_FAMILIES = {
    "бля": ("", "ть", "дь", "ди"),
    "сук": ("а", "и"),
    "пизд": ("а", "ец", "ець", "ёж", "еж", "ить", "ел", "ела"),
    "пізд": ("а", "ец", "ець", "ёж", "еж", "ить", "ел", "ела"),
    "пиздобол": ("", "а", "у", "ом", "е", "и", "ів", "ов"),
    "піздобол": ("", "а", "у", "ом", "е", "и", "ів", "ов"),
    "хуйн": ("я", "и", "ю", "е", "ёй", "ей"),
    "залуп": ("а", "ы", "е", "у", "ой", "ою"),
    "манд": ("а", "ы", "е", "у", "ой", "ою"),
    "пидарас": ("", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"),
    "пидорас": ("", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"),
    "уеб": ("ок", "ка", "ку", "ком", "ке", "ки", "ков", "кам", "ками", "ках"),
    "уёб": ("ок", "ка", "ку", "ком", "ке", "ки", "ков", "кам", "ками", "ках"),
    "убеб": ("ок", "ка", "ку", "ком", "ке", "ки", "ков", "кам", "ками", "ках"),
    "убёб": ("ок", "ка", "ку", "ком", "ке", "ки", "ков", "кам", "ками", "ках"),
    "еб": ("ать", "ал", "ала", "аный", "аная", "аное", "аные", "анутый", "анутся"),
    "заеб": ("ал", "ала", "али", "ись"),
    "мудак": ("", "и"),
    "долбоеб": ("", "ы"),
    "долбоёб": ("", "ы"),
    "fuck": ("", "s", "ed", "er", "ers", "ing"),
    "motherfuck": ("er", "ers", "ing"),
    "shit": ("", "s", "ty", "ting"),
    "bitch": ("", "es"),
    "asshole": ("", "s"),
    "cunt": ("", "s"),
    "dickhead": ("", "s"),
}
PROFANE_ABBREVIATIONS = {
    "wtf", "wtaf", "stfu", "gtfo", "ffs", "fml", "lmfao", "idgaf", "omfg",
    "пнх", "нхй", "пхй", "пздц", "пздец",
}
PROFANE_WORDS = {
    "дідько", "хуй", "хуя", "хуем", "хуёвый", "хуевый", "нахуй", "нахер", "нахёр",
    "похуй", "хер", "хёр", "хрен", "bullshit",
} | PROFANE_ABBREVIATIONS | {
    stem + ending for stem, endings in WORD_FAMILIES.items() for ending in endings
}
KNOWN_VARIANTS = r"бл[*•]+(?:ть|дь?)|х[*•]+й|е+б+а+ть|f[*•]+k(?:s|ed|er|ers|ing)?"
PROFANITY = re.compile(
    r"\b(?:" + "|".join(map(re.escape, sorted(PROFANE_WORDS))) + "|" + KNOWN_VARIANTS + r")\b"
)
DOTTED_ABBREVIATIONS = re.compile(
    r"\b(?:" + "|".join(r"\.".join(word) for word in sorted(PROFANE_ABBREVIATIONS)) + r")\b"
)
MASK_CHARS = "*•"
IN_WORD_MASK = re.compile(r"(?<=[^\W\d_])[*•]+(?=[^\W\d_])")
MASKED_TOKEN = re.compile(r"[\w*•]+")
MASK_RUN = re.compile(r"([*•]+)")
RUSSIAN_WORD = re.compile(r"\b[а-яёіїєґ]+\b")
TYPO_TARGETS = (
    "пиздец", "піздець", "пиздобол", "пидарас", "пидорас", "долбоеб", "долбоёб",
)


def one_letter_apart(word: str, target: str) -> bool:
    if abs(len(word) - len(target)) > 1:
        return False
    if len(word) < len(target):
        word, target = target, word
    for index, (left, right) in enumerate(zip(word, target)):
        if left == right:
            continue
        if len(word) != len(target):
            return word[index + 1:] == target[index:]
        return (
            word[index + 1:] == target[index + 1:]
            or (word[index:index + 2] == target[index:index + 2][::-1]
                and word[index + 2:] == target[index + 2:])
        )
    return len(word) != len(target)


def contains_link(entity_types: Iterable[str]) -> bool:
    return any(entity_type in {"url", "text_link"} for entity_type in entity_types)


def matches_masked_word(token: str) -> bool:
    if len(token) > 32:
        return False
    visible = token.replace("*", "").replace("•", "")
    if token == visible or not visible.isalpha():
        return False

    at_edge = token[0] in MASK_CHARS or token[-1] in MASK_CHARS
    if len(visible) < (4 if at_edge else 3):
        return False
    if token[0] in MASK_CHARS and token[-1] in MASK_CHARS:
        return False  # Markdown-style emphasis around a word.
    parts = MASK_RUN.split(token)
    pattern = re.compile("".join(
        f".{{1,{min(3, len(part) + 1)}}}" if index % 2 else re.escape(part)
        for index, part in enumerate(parts)
    ))
    return any(pattern.fullmatch(word) for word in PROFANE_WORDS)


def contains_profanity(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    if PROFANITY.search(normalized) or ("." in normalized and DOTTED_ABBREVIATIONS.search(normalized)):
        return True
    if any(character in normalized for character in MASK_CHARS):
        if any(matches_masked_word(token) for token in MASKED_TOKEN.findall(normalized)):
            return True
    unmasked = IN_WORD_MASK.sub("", normalized)
    if PROFANITY.search(unmasked):
        return True
    return any(
        word[:2] == target[:2]
        and (word[-1] == target[-1] or word[-2:] == target[-2:][::-1])
        and one_letter_apart(word, target)
        for word in RUSSIAN_WORD.findall(unmasked)
        if 5 <= len(word) <= 9
        for target in TYPO_TARGETS
    )

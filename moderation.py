import re
import unicodedata
from collections.abc import Iterable


PROFANITY = re.compile(
    r"\b(?:"
    r"бля(?:ть|дь|ди)?|бл[*•]+(?:ть|дь?)|сука|суки|п[иі]зд(?:а|ець?|ёж|еж|ить|ел|ела)|"
    r"п[иі]здобол(?:а|у|ом|е|и|ів|ов)?|дідько|"
    r"хуй|хуя|хуем|хуёвый|хуевый|хуйн(?:я|и|ю|е|ёй|ей)|нахуй|нах[её]р|похуй|"
    r"е+б+а+ть|еб(?:ал|ала|аный|аная|аное|аные|анутый|анутся)|"
    r"заеб(?:ал|ала|али|ись)|мудак|мудаки|долбо[её]б(?:ы)?|"
    r"f(?:u|[*•]+)(?:c|[*•]+)k(?:s|ed|er|ers|ing)?|motherfuck(?:er|ers|ing)|"
    r"shit(?:s|ty|ting)?|bullshit|bitch(?:es)?|asshole(?:s)?|"
    r"cunt(?:s)?|dickhead(?:s)?"
    r")\b"
)
IN_WORD_MASK = re.compile(r"(?<=[^\W\d_])[*•]+(?=[^\W\d_])")


def contains_link(entity_types: Iterable[str]) -> bool:
    return any(entity_type in {"url", "text_link"} for entity_type in entity_types)


def contains_profanity(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    return bool(PROFANITY.search(normalized) or PROFANITY.search(IN_WORD_MASK.sub("", normalized)))

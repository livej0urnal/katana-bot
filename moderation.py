import re
import unicodedata
from collections.abc import Iterable


PROFANITY = re.compile(
    r"\b(?:"
    r"бля(?:ть|дь|ди)?|сука|суки|пизд(?:а|ец|ёж|еж|ить|ел|ела)|"
    r"хуй|хуя|хуем|хуёвый|хуевый|хуйн(?:я|и|ю|е|ёй|ей)|нахуй|похуй|"
    r"еб(?:ать|ал|ала|аный|аная|аное|аные|анутый|анутся)|"
    r"заеб(?:ал|ала|али|ись)|мудак|мудаки|долбо[её]б(?:ы)?|"
    r"fuck(?:s|ed|er|ers|ing)?|motherfuck(?:er|ers|ing)|"
    r"shit(?:s|ty|ting)?|bullshit|bitch(?:es)?|asshole(?:s)?|"
    r"cunt(?:s)?|dickhead(?:s)?"
    r")\b"
)


def contains_link(entity_types: Iterable[str]) -> bool:
    return any(entity_type in {"url", "text_link"} for entity_type in entity_types)


def contains_profanity(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    return PROFANITY.search(normalized) is not None

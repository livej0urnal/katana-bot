from collections.abc import Iterable


def contains_link(entity_types: Iterable[str]) -> bool:
    return any(entity_type in {"url", "text_link"} for entity_type in entity_types)

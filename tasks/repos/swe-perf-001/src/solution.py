"""Duplicate detection utilities."""


def find_duplicates(items):
    """Return a list of items that appear more than once.

    Each duplicated value is reported once, in order of its first occurrence in
    ``items``.

    Args:
        items: An iterable of hashable values.

    Returns:
        A list of the duplicated values (one entry per duplicated value).
    """
    duplicates = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j]:
                if items[i] not in duplicates:
                    duplicates.append(items[i])
                break
    return duplicates

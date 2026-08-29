"""Nested dictionary traversal utilities."""


def deep_get(data, keys, default=None):
    """Safely traverse a nested dict using a list of keys.

    Args:
        data: The root dict (or value) to traverse.
        keys: An iterable of keys to follow in order.
        default: The value to return if any key is missing or a non-dict
            value is encountered mid-traversal.

    Returns:
        The value found at the end of the key path, or ``default`` if any
        key along the way is missing or the path cannot be followed.
    """
    for key in keys:
        # BUG: uses subscript access instead of a safe lookup, so a missing
        # intermediate key raises KeyError instead of returning ``default``.
        data = data[key]
    return data

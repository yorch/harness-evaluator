"""List processing utilities with pagination support."""


def get_page(items, page_number, page_size=10):
    """Return a page of items from a list.

    Args:
        items: The full list of items.
        page_number: 1-indexed page number to retrieve.
        page_size: Number of items per page (default 10).

    Returns:
        A list containing the items for the requested page.
        Returns an empty list if the page is beyond the available items.
    """
    start = (page_number - 1) * page_size
    end = page_number * page_size - 1  # BUG: off-by-one, should be page_number * page_size
    return items[start:end]

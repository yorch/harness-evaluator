"""CSV parsing utilities."""


def parse_csv_line(line, delimiter=","):
    """Parse a single CSV line into a list of fields.

    Args:
        line: A single CSV line string.
        delimiter: The field delimiter (default comma).

    Returns:
        A list of field strings.
    """
    # BUG: naive split does not handle quoted fields
    return line.split(delimiter)

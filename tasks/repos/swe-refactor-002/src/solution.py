"""A simple calculator with duplicated operand validation."""


class Calculator:
    """Arithmetic operations that validate their operands are numbers."""

    def add(self, x, y):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise TypeError("operands must be numbers")
        return x + y

    def subtract(self, x, y):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise TypeError("operands must be numbers")
        return x - y

    def multiply(self, x, y):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise TypeError("operands must be numbers")
        return x * y

    def divide(self, x, y):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise TypeError("operands must be numbers")
        if y == 0:
            raise ValueError("cannot divide by zero")
        return x / y

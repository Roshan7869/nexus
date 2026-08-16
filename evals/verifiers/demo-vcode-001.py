def verify(code: str) -> bool:
    return "def add" in code and "return a + b" in code

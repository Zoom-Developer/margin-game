def is_float(num: str) -> bool:
    try:
        float(num)
        return True
    except ValueError:
        return False
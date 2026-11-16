import json


def _to_klar(v):
    if v is None:
        return "JNull"
    if isinstance(v, bool):
        return ("JBool", v)
    if isinstance(v, (int, float)):
        return ("JNum", float(v))
    if isinstance(v, str):
        return ("JStr", v)
    if isinstance(v, list):
        return ("JArr", [_to_klar(x) for x in v])
    return ("JObj", {k: _to_klar(x) for k, x in v.items()})


def _from_klar(v):
    if v == "JNull":
        return None
    tag, val = v
    if tag == "JNum":
        return int(val) if float(val).is_integer() and abs(val) < 2**53 else val
    if tag == "JArr":
        return [_from_klar(x) for x in val]
    if tag == "JObj":
        return {k: _from_klar(x) for k, x in val.items()}
    return val


def _no_constant(name):
    # NaN and Infinity are not JSON; Node and the native runtime reject them too
    raise ValueError(f"{name} is not valid JSON")


def parse(text):
    return _to_klar(json.loads(text, parse_constant=_no_constant))


def stringify(value):
    from ..jsonconv import dumps      # numbers written like JavaScript, so every runtime agrees
    return dumps(_from_klar(value))


def pretty(value):
    from ..jsonconv import dumps
    return dumps(_from_klar(value), "  ")

"""Embedded independent Python runtime for generated source profile v4."""
import math
import re
import sys


def num(value):
    return ("n", value) if math.isfinite(value) else error("#NUM!")


def text_hex(value):
    return ("t", bytes.fromhex(value).decode("utf-8"))


def boolean(value):
    return ("b", value)


def blank():
    return ("z", None)


def error(code):
    return ("e", code)


def unref(value):
    while value[0] == "ref":
        value = value[1]
    return value


def number(value):
    value = unref(value)
    if value[0] in {"n", "e"}:
        return value
    if value[0] == "z":
        return num(0.0)
    if value[0] == "b":
        return num(float(value[1]))
    if value[0] == "t":
        text = value[1].strip()
        if not text:
            return num(0.0)
        if not re.fullmatch(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
            return error("#VALUE!")
        try:
            parsed = float(text)
        except ValueError:
            return error("#VALUE!")
        return num(parsed) if math.isfinite(parsed) else error("#VALUE!")
    return error("#VALUE!")


def comparison(left, right, operator):
    left,right=unref(left),unref(right)
    if left[0]=="e":
        return left
    if right[0]=="e":
        return right
    def empty(other):
        return ("t","") if other[0]=="t" else ("b",False) if other[0]=="b" else ("n",0.0)
    if left[0]=="z":
        left=empty(right)
    if right[0]=="z":
        right=empty(left)
    rank={"n":0,"t":1,"b":2}
    if left[0] not in rank or right[0] not in rank:
        return error("#VALUE!")
    if left[0]!=right[0]:
        order=-1 if rank[left[0]]<rank[right[0]] else 1
    else:
        a,b=left[1],right[1]
        if left[0]=="t":
            if a.isascii() and b.isascii():
                a,b=a.lower(),b.lower()
            a,b=a.encode("utf-8"),b.encode("utf-8")
        order=-1 if a<b else 1 if a>b else 0
    return boolean({"=":order==0,"<>":order!=0,"<":order<0,"<=":order<=0,">":order>0,">=":order>=0}[operator])


def equal(a,b):
    return comparison(a,b,"=")


def not_equal(a,b):
    return comparison(a,b,"<>")


def less(a,b):
    return comparison(a,b,"<")


def less_equal(a,b):
    return comparison(a,b,"<=")


def greater(a,b):
    return comparison(a,b,">")


def greater_equal(a,b):
    return comparison(a,b,">=")


def binary(left, right, operator):
    left, right = number(left), number(right)
    if left[0] == "e":
        return left
    if right[0] == "e":
        return right
    a, b = left[1], right[1]
    if operator == "/" and b == 0:
        return error("#DIV/0!")
    return num({"+": lambda: a + b, "-": lambda: a - b,
                "*": lambda: a * b, "/": lambda: a / b}[operator]())


def add(left, right):
    return binary(left, right, "+")


def sub(left, right):
    return binary(left, right, "-")


def mul(left, right):
    return binary(left, right, "*")


def div(left, right):
    return binary(left, right, "/")


def neg(value):
    value = number(value)
    return num(-float(value[1])) if value[0] == "n" else value


def positive(value):
    return number(value)


def if_value(condition, engine, yes, no):
    condition = unref(condition)
    if condition[0] == "e":
        return condition
    if condition[0] == "t" and condition[1] != "":
        return error("#VALUE!")
    if condition[0] not in {"n", "b", "z", "t"}:
        return error("#VALUE!")
    truth = condition[1] not in (None, False, 0, "")
    return unref(yes(engine) if truth else no(engine))


def if_error(value, engine, fallback):
    result = unref(fallback(engine) if unref(value)[0] == "e" else value)
    return ("t", "") if result[0] == "z" else result


def resolve(engine, value):
    if "!" in value:
        name = value.rsplit("!",1)[0]
        name = name[1:-1].replace("''", "'") if name.startswith("'") and name.endswith("'") else name
        name = engine.sheets[name.lower()]
        prefix = "'" + name.replace("'", "''") + "'!"
    else:
        prefix = engine.context
    return prefix + address(value)


def address(value):
    return value.rsplit("!", 1)[-1].replace("$", "").upper()


def coordinates(value):
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", address(value))
    column = 0
    for character in match[1]:
        column = column * 26 + ord(character) - 64
    return column, int(match[2])


def column_name(column):
    result = ""
    while column:
        column, digit = divmod(column - 1, 26)
        result = chr(65 + digit) + result
    return result


def reference(engine, value):
    item=engine.get(resolve(engine,value))
    engine.account(item)
    return ("ref",item)


def value_bytes(value):
    value = unref(value)
    return len(value[1].encode("utf-8")) if value[0] in {"t", "e"} else 8 if value[0] == "n" else 1


def range_ref(engine, value):
    start, end = value.split(":")
    a, b = coordinates(start), coordinates(end)
    assert (abs(a[0] - b[0]) + 1) * (abs(a[1] - b[1]) + 1) <= 100000
    prefix = resolve(engine,start).rsplit("!",1)[0] + "!"
    values, size = [], 0
    for row in range(min(a[1], b[1]), max(a[1], b[1]) + 1):
        for column in range(min(a[0], b[0]), max(a[0], b[0]) + 1):
            item = engine.get(prefix + column_name(column) + str(row))
            engine.account(item)
            size += value_bytes(item)
            if size > 1024 * 1024:
                raise ValueError("resource_limit: range payload exceeds 1 MiB")
            values.append(item)
    return ("range", values)


def sum_values(arguments):
    if not 1 <= len(arguments) <= 255:
        return error("#VALUE!")
    numbers = []
    for argument in arguments:
        reference_values = argument[0] in {"ref", "range"}
        values = argument[1] if argument[0] == "range" else [argument]
        for item in values:
            item = unref(item)
            if item[0] == "e":
                return item
            if reference_values and item[0] != "n":
                continue
            item = number(item)
            if item[0] == "e":
                return item
            numbers.append(item[1])
    total = 0.0
    for value in numbers:
        total += value
    return num(total)


class Engine:
    def __init__(self, defaults, formulas, sheets):
        self.sheets = {name.lower():name for name in sheets}
        self.context = ""
        self.defaults = defaults
        self.formulas = formulas
        self.cache = {}
        self.active = set()
        self.cache_bytes = 0
        self.materialized_bytes = 0

    def account(self, value):
        self.materialized_bytes += value_bytes(value)
        if self.materialized_bytes > 1024 * 1024:
            raise ValueError("resource_limit: materialized reference payload exceeds 1 MiB")

    def get(self, key):
        if key in self.cache:
            return self.cache[key]
        if key not in self.formulas:
            return self.defaults.get(key, blank())
        if key in self.active:
            raise ValueError("cycle: iterative calculation is not enabled")
        if len(self.active) >= 96:
            raise ValueError("resource_limit: dependency depth exceeds 96")
        self.active.add(key)
        previous = self.context
        self.context = key.rsplit("!",1)[0] + "!"
        try:
            value = unref(self.formulas[key](self))
        finally:
            self.context = previous
        if value[0] == "z":
            value = num(0.0)
        if value[0] == "range":
            value = error("#VALUE!")
        self.active.remove(key)
        self.cache_bytes += value_bytes(value)
        if self.cache_bytes > 1024 * 1024:
            raise ValueError("resource_limit: cached payload exceeds 1 MiB")
        self.cache[key] = value
        return value


def decode(value):
    tag, payload = value.split(":", 1)
    if tag == "n":
        return num(float(payload))
    if tag == "t":
        return text_hex(payload)
    if tag == "b" and payload in {"0", "1"}:
        return boolean(payload == "1")
    if tag == "z" and not payload:
        return blank()
    if tag == "e" and payload in {"#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#CALC!", "#NULL!", "#SPILL!"}:
        return error(payload)
    raise ValueError("invalid typed input")


def run(defaults, formulas, sheets):
    arguments = sys.argv[1:]
    if arguments == ["--stdin"]:
        data = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("resource_limit: input exceeds 1 MiB")
        arguments = data.decode("utf-8").splitlines()
    for argument in arguments:
        key, payload = argument.split("=", 1)
        key = bytes.fromhex(key).decode("utf-8")
        if key not in defaults:
            raise ValueError("unknown input address")
        defaults[key] = decode(payload)
    engine = Engine(defaults, formulas, sheets)
    output_bytes = 0
    for key in sorted(defaults.keys() | formulas.keys()):
        tag, value = unref(engine.get(key))
        if tag == "t":
            value = value.encode("utf-8").hex()
        elif tag == "b":
            value = "1" if value else "0"
        elif tag == "z":
            value = ""
        line = key.encode("utf-8").hex() + "\t" + tag + "\t" + str(value)
        output_bytes += len(line.encode("utf-8")) + 1
        if output_bytes > 1024 * 1024:
            raise ValueError("resource_limit: output exceeds 1 MiB")
        print(line)

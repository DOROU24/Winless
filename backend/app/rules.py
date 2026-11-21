"""Движок правил: валидация, проверка, объяснение, предпросмотр."""
from __future__ import annotations

from typing import Any

from .fields import EvalContext, FieldDef, field_value, parse_dt, _norm, _num

OPS_BY_TYPE = {
    "number": ["eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "not_in"],
    "text": ["eq", "neq", "contains", "not_contains", "starts_with", "in", "not_in"],
    "date": ["before", "after", "within_last_min", "older_than_min"],
    "flag": ["is_true", "is_false"],
}
ANY_OPS = ["is_empty", "not_empty"]
NO_VALUE_OPS = {"is_true", "is_false", "is_empty", "not_empty"}
LIST_OPS = {"in", "not_in"}

OP_LABELS = {
    "eq": "=", "neq": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "от … до",
    "in": "одно из", "not_in": "не одно из", "contains": "содержит", "not_contains": "не содержит",
    "starts_with": "начинается с", "before": "раньше", "after": "позже",
    "within_last_min": "за последние, мин", "older_than_min": "старше, мин", "is_true": "да", "is_false": "нет",
    "is_empty": "пусто", "not_empty": "не пусто",
}


class RuleError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


def is_group(node: dict) -> bool:
    return isinstance(node, dict) and "op" in node and "children" in node


def is_empty_rule(rule) -> bool:
    return rule is None or (is_group(rule) and not rule.get("children"))


# ---- валидация -----------------------------------------------------------------------

def validate(rule, reg: dict[str, FieldDef]) -> list[dict]:
    errors: list[dict] = []

    def err(path, msg):
        errors.append({"path": path, "message": msg})

    def walk(node, path):
        if node is None:
            return
        if not isinstance(node, dict):
            err(path, "Узел правила должен быть объектом")
            return
        if "op" in node or "children" in node:
            if node.get("op") not in ("and", "or"):
                err(path, f"Группа: оператор «{node.get('op')}» не поддерживается, нужен and или or")
            ch = node.get("children")
            if not isinstance(ch, list):
                err(path, "Группа: children должен быть списком")
                return
            for i, c in enumerate(ch):
                walk(c, f"{path}.children[{i}]")
            return
        key = node.get("field")
        fd = reg.get(key)
        where = f"Условие «{key}»"
        if fd is None:
            if isinstance(key, str) and key.startswith("user."):
                err(path, f"{where}: поле удалено. Дашборд только для просмотра, отметок координатора («Вижу») "
                          f"больше нет. Уберите это условие из правила")
            else:
                err(path, f"{where}: такого поля нет в реестре")
            return
        cmp_ = node.get("cmp")
        allowed = OPS_BY_TYPE.get(fd.type, []) + ANY_OPS
        if cmp_ not in allowed:
            err(path, f"{where}: оператор «{cmp_}» не подходит для типа «{fd.type}». Можно: {', '.join(allowed)}")
            return
        if fd.params:
            args = node.get("args") or {}
            for p in fd.params:
                if p["kind"] == "field":
                    if args.get(p["name"]) in (None, ""):
                        err(path, f"{where}: не заполнен параметр «{p['label']}»")
                elif args.get(p["name"]) is not None and not isinstance(args.get(p["name"]), list):
                    err(path, f"{where}: параметр «{p['label']}» должен быть списком")
        if cmp_ in NO_VALUE_OPS:
            return
        v = node.get("value")
        if cmp_ in LIST_OPS:
            if not isinstance(v, list) or not v:
                err(path, f"{where}: для «{OP_LABELS[cmp_]}» нужен непустой список значений")
            elif fd.type == "number" and any(_num(x) is None for x in v):
                err(path, f"{where}: в списке должны быть числа")
            return
        if cmp_ == "between":
            if not isinstance(v, list) or len(v) != 2 or any(_num(x) is None for x in v):
                err(path, f"{where}: для «от … до» нужны два числа")
            return
        if cmp_ in ("within_last_min", "older_than_min"):
            if _num(v) is None:
                err(path, f"{where}: укажите число минут")
            return
        if cmp_ in ("before", "after"):
            if parse_dt(v) is None:
                err(path, f"{where}: укажите дату в формате ISO, например 2026-11-21T10:00:00Z")
            return
        if fd.type == "number" and _num(v) is None:
            err(path, f"{where}: значение должно быть числом")
        if fd.type == "text" and (v is None or v == ""):
            err(path, f"{where}: укажите значение")

    walk(rule, "rule")
    return errors


# ---- проверка ----------------------------------------------------------------------------

def _cmp(fd: FieldDef, cmp_: str, actual: Any, value: Any, ctx: EvalContext) -> bool:
    if cmp_ == "is_empty":
        return actual is None or actual == ""
    if cmp_ == "not_empty":
        return not (actual is None or actual == "")
    if actual is None:
        return False
    t = fd.type
    if t == "flag":
        b = bool(actual)
        return b if cmp_ == "is_true" else not b
    if t == "date":
        d = parse_dt(actual)
        if d is None:
            return False
        if cmp_ == "before":
            return d < parse_dt(value)
        if cmp_ == "after":
            return d > parse_dt(value)
        age = (ctx.now - d).total_seconds() / 60
        if cmp_ == "within_last_min":
            return 0 <= age <= float(value)
        if cmp_ == "older_than_min":
            return age > float(value)
        return False
    if t == "number":
        a = _num(actual)
        if a is None:
            return False
        if cmp_ in LIST_OPS:
            hit = any(a == _num(x) for x in value)
            return hit if cmp_ == "in" else not hit
        if cmp_ == "between":
            lo, hi = sorted((_num(value[0]), _num(value[1])))
            return lo <= a <= hi
        v = _num(value)
        return {"eq": a == v, "neq": a != v, "gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v}.get(cmp_, False)
    # text
    a = str(actual)
    if cmp_ in LIST_OPS:
        hit = any(_norm(actual) == _norm(x) for x in value)
        return hit if cmp_ == "in" else not hit
    v = "" if value is None else str(value)
    al, vl = a.lower(), v.lower()
    return {
        "eq": al == vl, "neq": al != vl, "contains": vl in al, "not_contains": vl not in al,
        "starts_with": al.startswith(vl),
    }.get(cmp_, False)


def _leaf(node, inc, reg, ctx):
    fd = reg.get(node.get("field"))
    if fd is None or not fd.available:
        return False, None
    actual = field_value(reg, fd.key, inc, ctx, node.get("args"))
    return _cmp(fd, node.get("cmp"), actual, node.get("value"), ctx), actual


def match(rule, inc, reg, ctx) -> bool:
    if is_empty_rule(rule):
        return True
    if is_group(rule):
        ch = [c for c in rule["children"] if not is_empty_rule(c)]
        if not ch:
            return True
        if rule["op"] == "and":
            return all(match(c, inc, reg, ctx) for c in ch)
        return any(match(c, inc, reg, ctx) for c in ch)
    return _leaf(rule, inc, reg, ctx)[0]


# ---- объяснение ---------------------------------------------------------------------------

def fmt_value(fd: FieldDef, v: Any) -> str:
    if v is None:
        return "пусто"
    if fd.type == "flag":
        return "да" if v else "нет"
    if fd.labels:
        lab = fd.labels.get(str(v)) or fd.labels.get(str(_num(v)) if _num(v) is not None else "")
        if lab is None and _num(v) is not None and float(_num(v)).is_integer():
            lab = fd.labels.get(str(int(_num(v))))
        if lab:
            return f"{_fmt_num(v)} ({lab})"
    if isinstance(v, float):
        return _fmt_num(v)
    return str(v)


def _fmt_num(v):
    n = _num(v)
    if n is None:
        return str(v)
    if float(n).is_integer():
        return str(int(n))
    return f"{n:.1f}".replace(".", ",")


def describe_leaf(node, fd: FieldDef, reg) -> str:
    name = fd.label
    if fd.params:
        args = node.get("args") or {}
        inner = reg.get(args.get("field"))
        parts = [inner.label if inner else str(args.get("field"))]
        if args.get("from") or args.get("to"):
            parts.append(f"{'/'.join(map(str, args.get('from') or ['любое']))} → "
                         f"{'/'.join(map(str, args.get('to') or ['любое']))}")
        name = f"{fd.label} ({', '.join(parts)})"
    return name


def explain_leaf(node, fd, reg, actual) -> str:
    name = describe_leaf(node, fd, reg)
    cmp_ = node.get("cmp")
    if fd.type == "flag" and cmp_ in ("is_true", "is_false"):
        return name if cmp_ == "is_true" else f"{name}: нет"
    if cmp_ in ("is_empty", "not_empty"):
        return f"{name}: {'пусто' if cmp_ == 'is_empty' else fmt_value(fd, actual)}"
    unit = ""
    if fd.key.endswith("Pct"):
        unit = "%"
    elif fd.key.endswith("Min"):
        unit = " мин"
    for suffix in (", мин", ", % от срока", ", %"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            if "срока" in suffix:
                unit = "% от срока"
            break
    if fd.type == "date":
        return f"{name}: {actual}"
    shown = fmt_value(fd, actual)
    if fd.labels or fd.type == "text":
        return f"{name} = {shown}"
    return f"{name}: {shown}{unit}"


def explain(rule, inc, reg, ctx) -> list[str]:
    out: list[str] = []

    def walk(node) -> bool:
        if is_empty_rule(node):
            return True
        if is_group(node):
            ch = [c for c in node["children"] if not is_empty_rule(c)]
            if node["op"] == "and":
                ok = all(match(c, inc, reg, ctx) for c in ch)
                if ok:
                    for c in ch:
                        walk(c)
                return ok
            for c in ch:
                if match(c, inc, reg, ctx):
                    walk(c)
                    return True  # первой выполненной ветви достаточно для «почему здесь»
            return False
        ok, actual = _leaf(node, inc, reg, ctx)
        if ok:
            out.append(explain_leaf(node, reg[node["field"]], reg, actual))
        return ok

    walk(rule)
    return out


def rule_fields(rule) -> set[str]:
    """Какие поля использует правило (включая поля внутри hist.*)."""
    out: set[str] = set()

    def walk(n):
        if not isinstance(n, dict):
            return
        if is_group(n):
            for c in n["children"]:
                walk(c)
            return
        if n.get("field"):
            out.add(n["field"])
        if (n.get("args") or {}).get("field"):
            out.add(n["args"]["field"])

    walk(rule)
    return out


def missing_fields(rule, reg) -> list[str]:
    return sorted(f for f in rule_fields(rule) if f not in reg)


def or_of(rules: list[dict]) -> dict:
    rules = [r for r in rules if not is_empty_rule(r)]
    if len(rules) == 1:
        return rules[0]
    return {"op": "or", "children": rules}

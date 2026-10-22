"""Handcrafted finite abstractions; not automatic Java/Python translation."""
import cvc5
from cvc5 import Kind as K


class Model:
    def __init__(self, name):
        self.name = name
        self.tm = cvc5.TermManager()
        self.symbols = {}
        self.controls = set()
        self.background = []

    def var(self, name, sort='Bool', control=False):
        typ = self.tm.getBooleanSort() if sort == 'Bool' else self.tm.getIntegerSort()
        value = self.tm.mkConst(typ, name)
        self.symbols[name] = value
        if control:
            self.controls.add(name)
        return value

    def op(self, kind, *args):
        return self.tm.mkTerm(kind, *args)

    def neg(self, value):
        return self.op(K.NOT, value)

    def conjunction(self, *args):
        return self.op(K.AND, *args) if len(args) > 1 else (args[0] if args else self.tm.mkBoolean(True))

    def eq(self, a, b):
        return self.op(K.EQUAL, a, b)

    def integer(self, value):
        return self.tm.mkInteger(value)


def java_image():
    m = Model('java_image')
    f1 = m.var('from1_present', control=True)
    f2 = m.var('from2_present', control=True)
    null = m.var('version_null', control=True)
    throws = m.var('version_throws', control=True)
    v = m.var('version', 'Int', control=True)
    called = m.var('version_called')
    reached = m.var('image_observation_reached')
    selected = m.var('java8_selected')
    ext = m.var('external_image_matches')  # unknown, never a controllable response
    m.background = [
        m.op(K.GEQ, v, m.integer(-2147483648)),
        m.op(K.LEQ, v, m.integer(2147483647)),
        m.eq(called, m.neg(f1)),
        m.eq(reached, m.conjunction(called, m.neg(null), m.neg(throws))),
        m.eq(selected, m.conjunction(reached, m.op(K.LEQ, v, m.integer(8)))),
    ]
    m.guard = m.neg(f1)
    m.goal = selected
    m.candidate = m.conjunction(m.neg(null), m.neg(throws), m.op(K.LEQ, v, m.integer(8)))
    m.external_goal = m.op(K.OR, selected, m.conjunction(f1, f2, ext))
    return m


def java_entrypoint():
    m = Model('java_entrypoint')
    empty = m.var('custom_empty', control=True)
    custom = m.var('custom_matches', control=True)
    computed = m.var('computed_matches', control=True)
    selected = m.var('entrypoint_matches')
    reached = m.var('entrypoint_observation_reached')
    m.background = [
        m.op(K.IMPLIES, empty, m.neg(custom)),
        m.eq(reached, m.tm.mkBoolean(True)),  # explicit normal-getter/selection slice premise
        m.eq(selected, m.conjunction(reached, m.op(K.ITE, empty, computed, custom))),
    ]
    m.guard = m.tm.mkBoolean(True)
    m.goal = selected
    m.candidate = m.conjunction(m.neg(empty), custom)
    return m


def python_flow():
    m = Model('python_flow')
    none = m.var('ctx_none', control=True)
    at = m.var('abort_throws', control=True)
    am = m.var('abort_message_matches', control=True)
    ft = m.var('fallback_throws', control=True)
    fm = m.var('fallback_message_matches', control=True)
    ac = m.var('abort_called')
    fc = m.var('fallback_called')
    observed = m.var('exception_observed')
    matches = m.var('message_observed_matches')
    m.background = [
        m.eq(ac, m.neg(none)),
        m.eq(fc, m.op(K.OR, none, m.neg(at))),
        m.eq(observed, m.op(K.OR, m.conjunction(ac, at), m.conjunction(fc, ft))),
        m.eq(matches, m.op(K.OR, m.conjunction(ac, at, am), m.conjunction(fc, ft, fm))),
    ]
    m.guard = m.tm.mkBoolean(True)  # normal lookup and accessible attributes, explicitly scoped
    m.goal = m.conjunction(observed, matches)
    m.candidate = m.conjunction(m.neg(none), at, am)
    return m

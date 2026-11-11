"""Static-analysis input only. This module is never imported or executed by the pilot."""


def unknown_value():
    raise RuntimeError("opaque dependency placeholder; not an effect summary")


def observe(value):
    return value


class Box:
    pass


GLOBAL_BOX = Box()


def overwritten():
    value = unknown_value()
    value = 0
    return observe(value)


def forward(formal):
    return formal


def interprocedural(actual):
    returned = forward(actual)
    return observe(returned)


def early_exit(flag):
    if flag:
        return "early"
    observe("after")
    return "normal"


def explicit_raise():
    raise ValueError("stop")
    observe("unreachable")


def caught_and_finally(dependency, box):
    try:
        dependency.fill(box)
        observe("normal")
    except ValueError:
        observe(box.ready)
    finally:
        observe(box.ready)
    return box.ready


def visible_write(box):
    alias = box
    alias.ready = True
    return box.ready


def unrelated_write(box, other):
    other.ready = True
    return box.ready


def proven_fresh_write(box):
    fresh = Box()
    fresh.ready = True
    return box.ready


def unknown_write(dependency, box):
    returned = dependency.fill(box)
    return returned, box.ready


def unknown_may_write(dependency, box):
    dependency.mutate(box)
    return box.ready


def returned_alias_write(dependency, box):
    returned = dependency.alias(box)
    returned.ready = True
    return box.ready


def fresh_argument_does_not_reach(dependency, box):
    fresh = Box()
    dependency.mutate(fresh)
    return box.ready


def fresh_return_alias_does_not_reach(dependency, box):
    fresh = Box()
    returned = dependency.alias(fresh)
    returned.ready = True
    return box.ready


def definite_state_kill(dependency, box):
    dependency.mutate(box)
    box.ready = False
    return observe(box.ready)


def conditional_write_is_not_a_kill(dependency, box, flag):
    dependency.mutate(box)
    if flag:
        box.ready = False
    return box.ready


def conditional_then_definite_kill(dependency, box, flag):
    dependency.mutate(box)
    if flag:
        box.ready = True
    box.ready = False
    return box.ready


def returned_alias_other_field(dependency, box):
    returned = dependency.alias(box)
    returned.other = True
    return box.ready


def receiver_may_write(dependency):
    dependency.refresh()
    return dependency.configured


def receiver_other_object(dependency, other):
    dependency.refresh()
    return other.configured


def receiver_effect_overwritten(dependency):
    dependency.refresh()
    dependency.configured = False
    return dependency.configured


def receiver_loop_prewrite_is_not_a_kill(dependency, repeat):
    while True:
        dependency.configured = False
        dependency.refresh()
        if repeat:
            repeat = False
            continue
        break
    return dependency.configured


def global_may_write(dependency):
    dependency.synchronize()
    return GLOBAL_BOX.active


def local_object_is_not_global(dependency):
    GLOBAL_BOX = Box()
    dependency.synchronize()
    return GLOBAL_BOX.active


def global_effect_overwritten(dependency):
    dependency.synchronize()
    GLOBAL_BOX.active = False
    return GLOBAL_BOX.active


def escaped_argument_may_write(dependency, box):
    dependency.capture(box)
    dependency.apply()
    return box.sealed


def escaped_other_object(dependency, box, other):
    dependency.capture(box)
    dependency.apply()
    return other.sealed


def escaped_effect_overwritten(dependency, box):
    dependency.capture(box)
    dependency.apply()
    box.sealed = False
    return box.sealed


def visible_pure(value):
    return value


def visible_pure_then_observe(box):
    visible_pure(box)
    return box.stable


def global_rebind_boundary(dependency):
    global GLOBAL_BOX
    dependency.synchronize()
    GLOBAL_BOX = Box()
    return GLOBAL_BOX.active


def receiver_fresh_other(dependency):
    dependency.refresh()
    fresh = Box()
    return fresh.configured


def escaped_fresh_other(dependency, box):
    dependency.capture(box)
    dependency.apply()
    fresh = Box()
    return fresh.sealed


def direct_argument_loop_prewrite_is_not_a_kill(dependency, box, repeat):
    while True:
        box.ready = False
        dependency.mutate(box)
        if repeat:
            repeat = False
            continue
        break
    return box.ready

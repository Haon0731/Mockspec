def recursive_left(dependency, box, remaining):
    if remaining:
        return recursive_right(dependency, box, remaining - 1)
    dependency.mutate(box)
    return box.ready


def recursive_right(dependency, box, remaining):
    return recursive_left(dependency, box, remaining)

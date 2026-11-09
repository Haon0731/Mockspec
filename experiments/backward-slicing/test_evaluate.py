import pytest
from evaluate import inject


def test_inject_preserves_every_non_slot_character_and_rejects_extra_slot():
    names = [f'mock_stub_{i:03}' for i in range(1, 7)] + [f'verify_{i:03}' for i in range(1, 5)]
    parts = [f'    # === PY-SLOT-START:{name} ===\n    pass\n    # === PY-SLOT-END:{name} ==='
             for name in names]
    source = 'def test_example():\n' + '\n    assert 1 == 1\n'.join(parts) + '\n    assert True\n'
    slots = {name: 'x = 42' for name in names}
    expected = 'def test_example():\n' + '\n    assert 1 == 1\n'.join(['    x = 42']*10) + '\n    assert True\n'
    assert inject(source, slots) == expected
    with pytest.raises(ValueError, match='slot mismatch'):
        inject(source, {**slots, 'extra': 'pass'})

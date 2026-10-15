"""Independent acceptance owned by evaluator; never sent to repair model."""


def check(candidate):
    assert candidate([10.0, 20.0], 0.2) == 24.0
    assert candidate([], 0.5) == 0.0
    assert candidate([9.0], 1.0) == 0.0
    assert candidate([7.0], 0.0) == 7.0
    for invalid in [-0.01, 1.01]:
        try:
            candidate([5.0], invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid rate must be rejected")


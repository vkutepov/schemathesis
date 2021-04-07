import pytest
from deepdiff import DeepDiff


def test_diff_responses():
    assert DeepDiff(
        pytest.schemathesis['old'],
        pytest.schemathesis['new'],
        ignore_order=True) == {}

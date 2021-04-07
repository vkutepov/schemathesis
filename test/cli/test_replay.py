import pytest
from deepdiff import DeepDiff


def test_diff_responses():
    assert DeepDiff(
        pytest.my_global_variable['old'],
        pytest.my_global_variable['new'],
        ignore_order=True) == {}
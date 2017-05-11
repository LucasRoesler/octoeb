import pytest
from octoeb.utils.formatting import validate_version


@pytest.mark.parametrize('version_text', [
    '1.0.0',
    '17.11.01',
    '17.12.11',
])
def test_valid_version(version_text):
    assert validate_version(version_text) == True

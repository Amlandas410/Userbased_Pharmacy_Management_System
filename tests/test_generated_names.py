import re
def test_tenant_database_name_pattern():
    assert re.fullmatch(r"ph_[a-f0-9]{16}", "ph_0000000000000001")
    assert not re.fullmatch(r"ph_[a-f0-9]{16}", "other_database")
    assert not re.fullmatch(r"ph_[a-f0-9]{16}", "ph_0001; DROP DATABASE")

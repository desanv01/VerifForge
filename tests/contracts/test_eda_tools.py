import pytest
from veriforge.contracts.eda_tools import EdaRequirements,EdaConfiguration

def test_kicad_requirement_gap_is_explicit_and_profile_is_closed():
    assert EdaRequirements(domain='kicad').mappings==()
    with pytest.raises(ValueError):EdaRequirements(domain='kicad',part='invented')
    with pytest.raises(ValueError):EdaRequirements(domain='vivado',part='other')
    with pytest.raises(ValueError):EdaConfiguration(domain='vivado',profile='kicad10.configured.v1')

def test_wire_review_execution_claims_are_not_allowed():
    with pytest.raises(ValueError):EdaRequirements(domain='kicad',reviewed_by='client')
    with pytest.raises(ValueError):EdaConfiguration(domain='kicad',profile='kicad10.configured.v1',execution_status='completed')

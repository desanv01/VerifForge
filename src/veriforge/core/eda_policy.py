"""Host-installed EDA review capabilities; public records cannot grant review.

The trusted launcher reinstalls these pins after restart. Missing or changed
configuration fails closed. This registry authorizes reads, not tool execution.
"""
from types import MappingProxyType

from veriforge.contracts import canonical_hash
from veriforge.system_investigation import eda_records as eda

_reviews = MappingProxyType({})
_implementation = None


def configure_eda_reviews(authorities, *, implementation_sha256):
    global _reviews, _implementation
    if type(authorities) is not tuple or len(authorities) > 256:
        raise ValueError('Bounded host review tuple required')
    if implementation_sha256 != eda.implementation_hash():
        raise ValueError('EDA implementation differs from approved source')
    reviews = {}
    for authority in authorities:
        if type(authority) is not eda.EdaAuthority or authority.record_hash in reviews:
            raise ValueError('Distinct host EDA authorities required')
        reviews[authority.record_hash] = authority
    _reviews, _implementation = MappingProxyType(reviews), implementation_sha256


def authority_for(record):
    reviews, implementation = _reviews, _implementation
    if implementation is None or implementation != eda.implementation_hash():
        raise ValueError('Approved EDA implementation is unavailable')
    authority = reviews.get(canonical_hash(record))
    if authority is None:
        raise ValueError('Host EDA review is unavailable')
    eda._authority(record, authority)
    return authority

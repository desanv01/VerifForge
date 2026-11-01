"""Public reference intent only; no private flight-controller data or native claim."""
from .facts import MappingRequirement
from .investigation import identity

NETS = ("JA1", "JA7", "JA2", "JA8", "JA3", "JA9", "JA4", "JA10", "GND", "GND", "VCC3V3", "VCC3V3")


def reference_requirements(*, datasheet_sha256: str, intent_sha256: str,
                           reviewed_by: str) -> tuple[MappingRequirement, ...]:
    """Trusted reviewed-store caller supplies identities, not model/user claims.

    Carrier pin numbers are not host pin numbering. Same-number symbol/pad pairs
    here are explicit reviewed reference intent, never a generic numbering rule.
    """
    identity(datasheet_sha256); identity(intent_sha256)
    if not isinstance(reviewed_by, str) or not reviewed_by.strip():
        raise ValueError("reviewer required")
    # Native local labels are sheet-qualified, including /GND; no alias inference.
    return tuple(MappingRequirement(ref,str(n),str(n),"/"+net,"Samtec","TSW-106-07-G-D",
        "VerifForge:Header2x6_2.54_Unreviewed",datasheet_sha256,"series drawing DS and reviewed pin-map",reviewed_by,intent_sha256)
        for ref in ("J1","J2") for n,net in enumerate(NETS,1))


def reference_support() -> dict:
    return {"id":"samtec_2x6_reference_v1", "supported":"selected source pin/pad/net identity and configured report comparison",
            "manufacturer":"Samtec", "mpn":"TSW-106-07-G-D", "reference_kind":"new_design",
            "host_pin_order":[1,7,2,8,3,9,4,10,5,11,6,12],
            "native_candidate_execution":"not_established",
            "unknowns":["1.00mm CAD drill vs 1.02mm typical recommendation", "finished hole and lead tolerances",
                        "maximum body and mating envelope", "host/cable orientation and measured continuity",
                        "current/load/thermal/voltage limits"], "fabrication_approved":False,
            "physical_electrical_verdict":"inconclusive"}

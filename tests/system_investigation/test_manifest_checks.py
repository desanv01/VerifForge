"""Synthetic independent source fixtures; no native/system acceptance."""
import pytest
from veriforge.contracts import canonical_hash
from veriforge.contracts.engineering import SystemManifest
from veriforge.system_investigation.manifest_checks import ManifestCheck, check_manifest, affected_checks, record_hash


def fixture(quantity="register_address", shared=False, reviewed=True, mismatch=True):
    records = {"fw": {"value": 4096}, "rtl": {"value": 8192 if mismatch else 4096}, "req": {"expected": 4096}}
    if quantity in ("net", "package_pin"):
        records = {"fw": {"value": "A1"}, "rtl": {"value": "B2" if mismatch else "A1"}, "req": {"expected": "A1"}}
    facts = []
    for index, (key, data) in enumerate(records.items()):
        pin = record_hash(data)
        facts.append(dict(fact_id=key,domain="firmware" if key=="fw" else "rtl" if key=="rtl" else "system",
            native_schema_id="synthetic.v1",native_record_hash=pin,native_record=dict(artifact_id=key,sha256=pin,revision_id="v1"),
            category="requirement" if key=="req" else "observation", review_state="reviewed" if reviewed else "unreviewed",
            provenance=dict(state="complete", sources=[dict(artifact=dict(artifact_id="source-"+key,sha256=("a" if shared else "abc"[index])*64,revision_id="v1"),location=dict(kind="json_pointer",pointer=""))],
                extraction=dict(method="native_parser",extractor_id="synthetic",extractor_version="1",implementation_hash="d"*64))))
    manifest=SystemManifest(manifest_id="test",revision_id="v1",review_state="reviewed",reviewed_by="reviewer",facts=facts)
    check=ManifestCheck(check_id="address",quantity=quantity,fact_ids=("fw","rtl"),requirement_fact_ids=("req",),field="value",expected=records["fw"]["value"],reviewed_by="reviewer")
    return manifest,records,(check,)


@pytest.mark.parametrize("quantity",["register_address","clock_period_ps","net","package_pin"])
def test_independent_conflict_and_consistent_values(quantity):
    manifest,records,checks=fixture(quantity)
    result=check_manifest(manifest,records,checks)
    assert result["comparisons"][0]["verdict"]=="conflict"
    assert result["physical_verdict"]=="inconclusive"
    manifest,records,checks=fixture(quantity,mismatch=False)
    assert check_manifest(manifest,records,checks)["comparisons"][0]["verdict"]=="consistent"


@pytest.mark.parametrize("kwargs",[{"shared":True},{"reviewed":False}])
def test_common_or_unreviewed_cannot_authorize_independent_result(kwargs):
    manifest,records,checks=fixture(**kwargs)
    row=check_manifest(manifest,records,checks)["comparisons"][0]
    assert row["value_comparison"]=="conflict" and row["verdict"]=="inconclusive"


def test_unbound_bytes_fail_as_error_and_correction_requires_rerun():
    before,records,checks=fixture()
    records["rtl"]["value"]=4096
    with pytest.raises(ValueError,match="identity"):check_manifest(before,records,checks)
    after,records,checks=fixture(mismatch=False)
    plan=affected_checks(before,after,checks)
    assert plan["required_check_ids"]==["address"] and plan["status"]=="pending_reruns"
    assert plan["acceptance"]=="not_established"


def test_downstream_rerun_closure():
    before,_,checks=fixture()
    after,_,_=fixture(mismatch=False)
    first=checks[0].model_copy(update={"rerun_check_ids":("board",)})
    second=checks[0].model_copy(update={"check_id":"board"})
    assert affected_checks(before,after,(first,second))["required_check_ids"]==["address","board"]


def test_unreviewed_binding_and_missing_value_stay_inconclusive():
    manifest,records,checks=fixture()
    check=checks[0].model_copy(update={"reviewed_by":None})
    assert check_manifest(manifest,records,(check,))["comparisons"][0]["verdict"]=="inconclusive"
    check=checks[0].model_copy(update={"field":"absent"})
    assert check_manifest(manifest,records,(check,))["comparisons"][0]["value_comparison"]=="inconclusive"


def test_clock_literal_extraction_never_evaluates_tcl():
    from veriforge.system_investigation.xdc import extract_clock_periods
    data=b"create_clock -name sys_clk -period 10.125 [get_ports {clk}];\n"
    assert extract_clock_periods(data,("clk",))[0]["period_ps"]==10125
    for bad in (data+data,b"source external.tcl",b"create_clock -period $x [get_ports {clk}]",b"create_clock -period 0 [get_ports {clk}]"):
        with pytest.raises(ValueError): extract_clock_periods(bad,("clk",))

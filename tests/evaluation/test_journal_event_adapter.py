"""Host-installed event schemas reuse the existing bounded, locked journal."""
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from veriforge.evaluation.harness import HarnessError, Journal
from veriforge.evaluation.records import RecordModel


class FamilyEvent(RecordModel):
    kind: Literal['family-test'] = 'family-test'
    case_id: str


def test_custom_schema_roundtrip_reopen_and_fifo_isolation(tmp_path):
    adapter = TypeAdapter(FamilyEvent)
    journal = Journal(tmp_path, event_adapter=adapter)
    journal.create()
    event = FamilyEvent(case_id='case-0001')
    with journal.locked() as session:
        head = session.append(event)
    assert Journal(tmp_path, event_adapter=adapter).read() == ((event,), head)
    with pytest.raises(ValidationError):
        Journal(tmp_path).read()


def test_custom_schema_preserves_hash_chain_validation(tmp_path):
    journal = Journal(tmp_path, event_adapter=TypeAdapter(FamilyEvent))
    journal.create()
    with journal.locked() as session:
        session.append(FamilyEvent(case_id='case-0001'))
    journal.path.write_bytes(journal.path.read_bytes().replace(b'case-0001', b'case-0002'))
    with pytest.raises(HarnessError, match='journal_chain'):
        journal.read()


def test_custom_schema_preserves_session_ownership(tmp_path):
    journal = Journal(tmp_path, event_adapter=TypeAdapter(FamilyEvent))
    journal.create()
    with journal.locked() as session:
        with pytest.raises(HarnessError, match='journal_event_invalid'):
            session.append({'kind': 'other', 'case_id': 'case-0001'})
    with pytest.raises(HarnessError, match='journal_session_inactive'):
        session.append(FamilyEvent(case_id='case-0001'))
    assert journal.read() == ((), None)


def test_schema_hook_does_not_accept_import_paths_or_arbitrary_objects(tmp_path):
    for invalid in ('some.module:adapter', object()):
        with pytest.raises(HarnessError, match='journal_event_adapter_invalid'):
            Journal(tmp_path, event_adapter=invalid)

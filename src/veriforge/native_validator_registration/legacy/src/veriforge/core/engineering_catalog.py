"""Trusted local recorded-project creation; never installs an executable adapter."""
from typing import Annotated

from pydantic import Field, model_validator
from sqlalchemy.exc import IntegrityError

from veriforge.adapters import registered_adapters
from veriforge.contracts import Identifier, WireModel
from veriforge.contracts.engineering import Domain
from .database import Project


class EngineeringProjectSpec(WireModel):
    project_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=256)]
    description: Annotated[str, Field(max_length=1000)] = ''
    domains: Annotated[tuple[Domain, ...], Field(min_length=1, max_length=6)]

    @model_validator(mode='after')
    def meaningful_metadata(self):
        if not self.name.strip() or len(set(self.domains)) != len(self.domains):
            raise ValueError('Project name and unique domains are required')
        return self


def _data(spec):
    return dict(id=spec.project_id, name=spec.name, description=spec.description, domains=list(spec.domains))


def project_view(project):
    """Canonical columns override no conflicting claims; legacy fields survive."""
    from . import service
    if project.owner != 'local':
        raise service.NotFound(project.id)
    if not isinstance(project.data, dict) or project.kind not in {'rtl_fixture', 'engineering_recorded'}:
        raise service.Conflict('Project metadata is unavailable or inconsistent')
    canonical = dict(id=project.id, kind=project.kind, contract_id=project.contract_id)
    if any(key in project.data and project.data[key] != value for key, value in canonical.items()):
        raise service.Conflict('Project metadata conflicts with canonical association')
    if project.kind == 'rtl_fixture':
        if not project.contract_id:
            raise service.Conflict('RTL fixture has no reviewed contract')
    else:
        if project.contract_id is not None:
            raise service.Conflict('Recorded project cannot have an RTL contract')
        try:
            spec = EngineeringProjectSpec(project_id=project.id, name=project.data['name'],
                description=project.data['description'], domains=project.data['domains'])
            if project.data != _data(spec):
                raise ValueError('Unexpected recorded project metadata')
        except (KeyError, ValueError, TypeError):
            raise service.Conflict('Recorded project metadata is unavailable or inconsistent') from None
    return {**project.data, **canonical}


def get_engineering_project(session, project_id, *, expected=None):
    from . import service
    row = session.get(Project, project_id)
    if row is None or row.owner != 'local':
        raise service.NotFound(project_id)
    project_view(row)
    if row.kind != 'engineering_recorded':
        raise service.Conflict('Project ID already identifies an RTL fixture')
    if expected is not None:
        if not isinstance(expected, EngineeringProjectSpec):
            raise ValueError('Expected a typed engineering project specification')
        checked = EngineeringProjectSpec.model_validate_json(expected.model_dump_json())
        if row.id != checked.project_id or row.data != _data(checked):
            raise service.Conflict('Recorded project metadata differs from expected immutable content')
    return row


def create_engineering_project(session, spec):
    """One caller transaction; exact repeats return prior, changes conflict.

    This is trusted service code only. No public write endpoint or tool action.
    """
    from . import service
    if not isinstance(spec, EngineeringProjectSpec):
        raise ValueError('Expected a typed engineering project specification')
    spec = EngineeringProjectSpec.model_validate_json(spec.model_dump_json())
    if spec.project_id in {fixture for adapter in registered_adapters() for fixture in adapter.fixture_ids}:
        raise service.Conflict('Recorded project cannot reuse a curated RTL fixture ID')
    if session.get(Project, spec.project_id) is not None:
        return get_engineering_project(session, spec.project_id, expected=spec)
    connection = session.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    try:
        with session.begin_nested():
            row = Project(id=spec.project_id, owner='local', kind='engineering_recorded', contract_id=None, data=_data(spec))
            session.add(row)
            session.flush()
    except IntegrityError:
        return get_engineering_project(session, spec.project_id, expected=spec)
    return row

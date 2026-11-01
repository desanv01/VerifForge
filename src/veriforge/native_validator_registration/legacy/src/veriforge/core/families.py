"""Only static reviewed descriptors cross the durable campaign boundary."""
from veriforge.adapters import get_adapter, registered_adapters

FIFO_ADAPTER = "rtl.fifo.v1"


def experiment_adapter(experiment):
    for descriptor in registered_adapters():
        if isinstance(experiment, descriptor.experiment_model):
            return descriptor
    raise ValueError("Experiment has no trusted family descriptor")


def project_adapter(project):
    from .engineering_catalog import project_view
    from .service import Conflict
    if project_view(project)['kind'] != 'rtl_fixture':
        raise Conflict('Recorded engineering projects do not support RTL execution')
    return get_adapter(project.data.get("adapter_id", FIFO_ADAPTER))


def campaign_contract(session, campaign, experiment):
    from .database import Contract
    descriptor = get_adapter(campaign.adapter_id)
    stored = descriptor.contract_model.model_validate(session.get(Contract, campaign.contract_id).data)
    return stored.model_copy(update={"parameters": experiment.parameters})

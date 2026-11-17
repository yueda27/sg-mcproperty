from sg_mcproperty.loaders.client import URAClient


def load():
    with URAClient() as ura:
        projects = ura.private_transactions()
    return projects

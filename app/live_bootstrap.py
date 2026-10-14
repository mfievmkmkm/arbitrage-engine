from dataclasses import dataclass
from .private_registry import PrivateRegistry
from .startup_reconcile import evaluate_snapshot,all_ready
@dataclass
class BootstrapResult:
 configured:bool
 ready:bool
 venues:list
 snapshot:dict
async def bootstrap(readers):
 if not readers:return BootstrapResult(False,False,[],{})
 registry=PrivateRegistry()
 for name,reader in readers.items():registry.add(name,reader)
 snapshot=await registry.snapshot()
 venues=evaluate_snapshot(snapshot)
 return BootstrapResult(True,all_ready(venues),venues,snapshot)

from dataclasses import dataclass
@dataclass(frozen=True)
class VenueApproval:
 approved:bool
 reason:str
def approve(probe,client_id_verified=False,reduce_only_verified=False):
 if not probe.private_positions:return VenueApproval(False,"PRIVATE_POSITIONS_UNAVAILABLE")
 if not client_id_verified:return VenueApproval(False,"CLIENT_ID_NOT_VERIFIED")
 if not reduce_only_verified:return VenueApproval(False,"REDUCE_ONLY_NOT_VERIFIED")
 return VenueApproval(True,"APPROVED")

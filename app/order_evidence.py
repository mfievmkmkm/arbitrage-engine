from dataclasses import dataclass
@dataclass(frozen=True)
class OrderEvidence:
 safe:bool
 reason:str
def validate(intent_id,result,requested_qty):
 if result is None:return OrderEvidence(False,"ORDER_RESULT_MISSING")
 if not result.order_id:return OrderEvidence(False,"EXCHANGE_ORDER_ID_MISSING")
 if result.filled<0 or result.filled>requested_qty+1e-12:return OrderEvidence(False,"INVALID_FILLED_QTY")
 if result.filled>0 and result.avg_price is None:return OrderEvidence(False,"FILLED_PRICE_MISSING")
 return OrderEvidence(True,"OK")

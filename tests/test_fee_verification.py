from app.fee_verification import from_trading_fee
def test_account_fee_must_be_explicit():
 assert from_trading_fee({"maker":.001,"taker":.002}).verified
 assert not from_trading_fee({}).verified

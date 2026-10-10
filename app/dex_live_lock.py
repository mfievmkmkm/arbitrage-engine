def allowed(config_enabled,dedicated_acceptance,wallet_isolated,proof):
 return bool(config_enabled and dedicated_acceptance and wallet_isolated and proof.allowed)

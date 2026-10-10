def allowed(config_enabled,manifest_valid,acceptance_passed,operator_armed):
 return bool(config_enabled and manifest_valid and acceptance_passed and operator_armed)

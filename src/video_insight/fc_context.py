from contextvars import ContextVar

# Define context variables to store FC context from request headers
fc_request_id = ContextVar("fc_request_id", default=None)
fc_function_name = ContextVar("fc_function_name", default=None)
fc_service_name = ContextVar("fc_service_name", default=None)
fc_region = ContextVar("fc_region", default=None)
fc_account_id = ContextVar("fc_account_id", default=None)

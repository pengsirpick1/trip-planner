"""Attribute the observed error boundary separately from its likely cause."""
import re


def redact(text):
    text = str(text)
    return re.sub(r'(?i)([?&](?:key|api_key|token)=)[^&\s\"\']+', r'\1[REDACTED]', text)[:4000]


def diagnose(message, *, layer='mcp_tool', code=None):
    message = redact(message)
    upper = message.upper()
    result = dict(layer=layer, category='unknown', attribution='undetermined',
                  provider_code=str(code) if code is not None else None,
                  message=message, retryable=False, action_required=None)
    # Symbolic upstream codes survive servers that flatten AMap JSON into text.
    categories = [
        ('quota', ['INSUFFICIENT_ABROAD_BALANCE', 'INSUFFICIENT_BALANCE', 'INSUFFICIENT_QUOTA', 'USER_DAILY_QUERY_OVER_LIMIT', 'DAILY_QUERY_OVER_LIMIT', 'IP_QUERY_OVER_LIMIT', '余额不足']),
        ('authentication', ['INVALID_USER_KEY', 'USERKEY_PLAT_NOMATCH', 'INVALID_USER_DOMAIN', 'INVALID_USER_IP']),
        ('rate_limit', ['ACCESS_TOO_FREQUENT', 'QPS_HAS_EXCEEDED_THE_LIMIT', 'TOO_MANY_REQUESTS']),
        ('invalid_arguments', ['INVALID_PARAMS', 'MISSING_REQUIRED_PARAMS', 'ILLEGAL_REQUEST', 'INVALID_ARGUMENTS']),
    ]
    for category, tokens in categories:
        match = next((t for t in tokens if t in upper), None)
        if match:
            result.update(category=category, attribution='upstream_service' if category != 'invalid_arguments' else 'local_request',
                          provider_code=str(code) if code is not None else match)
            break
    if result['category'] == 'quota':
        result['action_required'] = '检查上游账号的余额、配额和接口权限；确认后再充值或调整套餐。'
    elif result['category'] == 'authentication':
        result['action_required'] = '检查上游 key、IP/域名白名单和服务授权。'
    elif result['category'] == 'rate_limit':
        result['retryable'] = True
    elif layer in {'local_validation', 'local_routing'}:
        result.update(attribution='local_code', category='invalid_arguments' if layer == 'local_validation' else 'routing')
    elif layer == 'mcp_transport':
        # A timeout/EOF alone does not prove AMap rejected a request.
        result.update(category='transport', attribution='undetermined')
    elif layer == 'mcp_protocol':
        result.update(category='protocol', attribution='mcp_server_or_client')
    elif layer == 'upstream_service':
        result['attribution'] = 'upstream_service'
    return result

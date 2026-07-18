def normalize_msisdn(phone, default_cc='91'):
    """
    Normalize a phone number to WhatsApp's digit-only MSISDN form.

    Examples:
        9423217356     -> 919423217356
        +91 9423217356 -> 919423217356
        0919423217356  -> 919423217356
    """
    digits = ''.join(filter(str.isdigit, str(phone or '')))
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10 and default_cc:
        digits = f'{default_cc}{digits}'
    return digits

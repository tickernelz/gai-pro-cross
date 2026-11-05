from gai_pro.redact import mask_card, mask_email, redact


def test_card_number_keeps_only_last_four():
    assert mask_card("4242 4242 4242 4242") == "**** **** **** 4242"
    assert redact("paid with 4242424242424242 today") == "paid with **** **** **** 4242 today"


def test_password_values_are_replaced():
    assert redact("password: hunter2") == "password: ***"
    assert redact("totp=JBSWY3DPEHPK3PXP") == "totp: ***"


def test_email_is_masked_but_domain_survives():
    assert mask_email("someone@example.com") == "s***@example.com"
    assert redact("login for someone@example.com ok") == "login for s***@example.com ok"


def test_plain_text_is_untouched():
    assert redact("step 2/5: subscribing 12 months") == "step 2/5: subscribing 12 months"

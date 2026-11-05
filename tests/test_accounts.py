from gai_pro.accounts import merge_accounts, normalize_email, parse_batch, parse_line


def test_parses_four_field_format():
    item = parse_line("user@example.com----secret----recovery@example.com----JBSWY3DPEHPK3PXP")
    assert item["email"] == "user@example.com"
    assert item["password"] == "secret"
    assert item["recovery_email"] == "recovery@example.com"
    assert item["totp_secret"] == "JBSWY3DPEHPK3PXP"


def test_parses_three_field_format_with_totp():
    item = parse_line("user@example.com----secret----JBSWY3DPEHPK3PXP")
    assert item["totp_secret"] == "JBSWY3DPEHPK3PXP"
    assert item["recovery_email"] == ""


def test_parses_minimal_pair():
    item = parse_line("user@example.com----secret")
    assert item["recovery_email"] == ""
    assert item["totp_secret"] == ""


def test_accepts_alternate_delimiters():
    for line in (
        "user@example.com|secret|recovery@example.com",
        "user@example.com\tsecret\trecovery@example.com",
        "user@example.com,secret,recovery@example.com",
        "label\tuser@example.com\tsecret",
    ):
        item = parse_line(line)
        assert item is not None, line
        assert item["email"].endswith("example.com")


def test_ignores_blank_and_comment_lines():
    parsed, rejected = parse_batch("\n# note\n\nuser@example.com----secret\n")
    assert len(parsed) == 1
    assert rejected == []


def test_rejects_lines_without_credentials():
    parsed, rejected = parse_batch("just-a-label\n")
    assert parsed == []
    assert rejected == ["just-a-label"]


def test_merge_updates_existing_and_counts_new():
    first, added = merge_accounts([], parse_batch("a@example.com----one")[0])
    assert added == 1
    second, added = merge_accounts(first, parse_batch("a@example.com----two\nb@example.com----three")[0])
    assert added == 1
    assert len(second) == 2
    assert second[0]["password"] == "two"


def test_dedupe_ignores_dots_in_local_part():
    accounts, _ = merge_accounts([], parse_batch("a.b@example.com----one")[0])
    accounts, added = merge_accounts(accounts, parse_batch("ab@example.com----two")[0])
    assert added == 0
    assert len(accounts) == 1
    assert normalize_email("A.B@Example.com") == "ab@example.com"


def test_ids_are_stable_for_same_email():
    assert parse_line("a@example.com----x")["id"] == parse_line("a@example.com----y")["id"]

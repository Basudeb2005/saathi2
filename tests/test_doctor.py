from saathi.doctor import FAIL, OK, WARN, Check, Result, run_check


def test_passing_check_passes_through():
    assert run_check(Check("x", lambda: Result(OK, "fine"))).status == OK


def test_a_crashing_check_reports_itself_not_the_subject():
    result = run_check(Check("x", lambda: 1 / 0))
    assert result.status == FAIL
    assert "check itself errored" in result.detail


def test_optional_failures_are_downgraded_to_warnings():
    assert run_check(Check("x", lambda: Result(FAIL, "nope"), optional=True)).status == WARN


def test_optional_downgrade_keeps_the_fix_text():
    result = run_check(Check("x", lambda: Result(FAIL, "nope", "do this"), optional=True))
    assert result.fix == "do this"


def test_required_failures_stay_failures():
    assert run_check(Check("x", lambda: Result(FAIL, "nope"))).status == FAIL


def test_optional_check_that_passes_is_untouched():
    assert run_check(Check("x", lambda: Result(OK, "fine"), optional=True)).status == OK

from saathi.setup import (
    SECTIONS,
    check_livekit_url,
    mask,
    missing_required,
    read_env,
    render_env,
    require_prefix,
)


# ---- masking -----------------------------------------------------------

def test_mask_shows_the_ends_only():
    assert mask("sk-proj-abcdefghijklmnop") == "sk-p...mnop"


def test_mask_hides_short_values_entirely():
    assert mask("secret") == "******"


def test_mask_of_empty_is_empty():
    assert mask("") == ""


# ---- validation --------------------------------------------------------

def test_prefix_validator_accepts_a_good_key():
    assert require_prefix("sk-", "an OpenAI key")("sk-proj-abc") is None


def test_prefix_validator_explains_a_bad_key():
    assert "sk-" in require_prefix("sk-", "an OpenAI key")("oops")


def test_validators_pass_empty_values_through():
    assert require_prefix("sk-", "x")("") is None
    assert check_livekit_url("") is None


def test_livekit_url_wants_websockets():
    assert check_livekit_url("https://x.livekit.cloud") is not None
    assert check_livekit_url("wss://x.livekit.cloud") is None


# ---- reading -----------------------------------------------------------

def test_read_missing_file_is_empty(tmp_path):
    assert read_env(tmp_path / "nope") == {}


def test_read_skips_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nOPENAI_API_KEY=sk-1\n")
    assert read_env(p) == {"OPENAI_API_KEY": "sk-1"}


def test_read_strips_quotes(tmp_path):
    p = tmp_path / ".env"
    p.write_text('OPENAI_API_KEY="sk-1"\n')
    assert read_env(p)["OPENAI_API_KEY"] == "sk-1"


def test_read_tolerates_a_malformed_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("garbage\nOPENAI_API_KEY=sk-1\n")
    assert read_env(p)["OPENAI_API_KEY"] == "sk-1"


def test_values_containing_equals_survive(tmp_path):
    p = tmp_path / ".env"
    p.write_text("LIVEKIT_API_SECRET=abc=def=\n")
    assert read_env(p)["LIVEKIT_API_SECRET"] == "abc=def="


# ---- rendering ---------------------------------------------------------

def test_render_omits_empty_values():
    assert "DEEPGRAM_API_KEY" not in render_env({"OPENAI_API_KEY": "sk-1", "DEEPGRAM_API_KEY": ""})


def test_render_keeps_unknown_hand_added_keys():
    assert "MY_OWN_VAR=1" in render_env({"OPENAI_API_KEY": "sk-1", "MY_OWN_VAR": "1"})


def test_render_round_trips(tmp_path):
    values = {"OPENAI_API_KEY": "sk-1", "LIVEKIT_URL": "wss://x", "MY_OWN_VAR": "7"}
    p = tmp_path / ".env"
    p.write_text(render_env(values))
    assert read_env(p) == values


def test_render_drops_a_section_with_nothing_in_it():
    assert "ElevenLabs" not in render_env({"OPENAI_API_KEY": "sk-1"})


# ---- required fields ---------------------------------------------------

def test_missing_required_lists_every_gap():
    assert set(missing_required({})) == {
        "OPENAI_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    }


def test_missing_required_is_empty_when_complete():
    assert missing_required({
        "OPENAI_API_KEY": "sk-1", "LIVEKIT_URL": "wss://x",
        "LIVEKIT_API_KEY": "APIx", "LIVEKIT_API_SECRET": "s",
    }) == []


def test_optional_sections_are_never_required():
    required = {f.key for s in SECTIONS if not s.optional for f in s.fields}
    assert "DEEPGRAM_API_KEY" not in required
    assert "LIVEKIT_SIP_TRUNK_ID" not in required

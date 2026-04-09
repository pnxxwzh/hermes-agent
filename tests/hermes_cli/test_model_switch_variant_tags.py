"""Regression tests for aggregator model shorthand and variant tags."""

from hermes_cli.model_switch import switch_model


def test_openrouter_slug_with_variant_tag_is_preserved():
    result = switch_model(
        raw_input="nvidia/nemotron-3-super-120b-a12b:free",
        current_provider="openrouter",
    )
    assert result.success is True
    assert result.new_model == "nvidia/nemotron-3-super-120b-a12b:free"


def test_aggregator_vendor_model_shorthand_converts_to_slug():
    result = switch_model(
        raw_input="nvidia:nemotron-3-super-120b-a12b",
        current_provider="openrouter",
    )
    assert result.success is True
    assert result.new_model == "nvidia/nemotron-3-super-120b-a12b"


def test_aggregator_vendor_model_shorthand_preserves_variant_suffix():
    result = switch_model(
        raw_input="nvidia:nemotron-3-super-120b-a12b:free",
        current_provider="openrouter",
    )
    assert result.success is True
    assert result.new_model == "nvidia/nemotron-3-super-120b-a12b:free"


def test_non_aggregator_provider_does_not_rewrite_colon_input():
    result = switch_model(
        raw_input="nvidia:nemotron-3-super-120b-a12b:free",
        current_provider="anthropic",
    )
    assert result.success is True
    assert result.new_model == "nvidia:nemotron-3-super-120b-a12b:free"

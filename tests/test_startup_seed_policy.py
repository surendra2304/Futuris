"""Startup policy tests that do not touch the configured application database."""

from futuris.demo.startup_policy import should_seed_demo_on_startup


def test_demo_seeding_is_off_by_default_for_production():
    assert not should_seed_demo_on_startup("production", enabled=False)


def test_demo_seeding_is_never_allowed_in_production_even_when_enabled():
    assert not should_seed_demo_on_startup("production", enabled=True)
    assert not should_seed_demo_on_startup("PROD", enabled=True)


def test_demo_seeding_requires_explicit_opt_in_outside_production():
    assert not should_seed_demo_on_startup("development", enabled=False)
    assert should_seed_demo_on_startup("development", enabled=True)

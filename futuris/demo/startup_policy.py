"""Policy helpers for controlling optional startup demo data generation."""


def should_seed_demo_on_startup(app_env: str, enabled: bool) -> bool:
    """Permit opt-in demo seeding only outside production environments."""
    return enabled and app_env.strip().lower() not in {"prod", "production"}

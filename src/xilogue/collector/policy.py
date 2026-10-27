"""Policy is shared by capture admission and physical submission."""


def inspect_target(monitor, config, expected=None, restrict=None):
    try:
        if not monitor.desktop_available():
            return None, "desktop_unavailable"
        target = monitor.foreground()
        if target is None:
            return None, "target_invalid"
        if not target.title.strip() or not target.app_name:
            return None, "target_unknown"
        if target.app_name.casefold() in config.excluded_apps:
            return None, "excluded"
        if restrict is not None and not restrict(target):
            return None, "target_restricted"
        if expected is not None and target != expected:
            return None, "target_changed"
        if config.idle_policy == "pause" and monitor.idle_seconds() >= config.idle_seconds:
            return None, "idle"
        return target, "allowed"
    except Exception:
        return None, "monitor_error"

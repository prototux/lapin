"""Per-person service accounts (Jellyfin, Subsonic / Navidrome...). Stored
in each user's settings; a request uses the speaker's own account, else the
household's (shared) one."""

SERVICES = {
    "jellyfin": {"title": "Jellyfin", "fields": [("url", "Server URL, e.g. http://jellyfin.local:8096"),
                                                 ("username", "User name"), ("password", "Password (secret)"),
                                                 ("api_key", "or an API key (secret)")],
                 "required": ("url",)},
    "subsonic": {"title": "Navidrome / Subsonic", "fields": [("url", "Server URL, e.g. http://navidrome.local:4533"),
                                                             ("username", "User name"),
                                                             ("password", "Password (secret)")],
                 "required": ("url", "username", "password")},
}
SECRET = ("password", "api_key")


def account(app, user, service):
    """The usable account for `user` (or the household's), or None."""
    req = SERVICES[service]["required"]
    others = [u["name"] for u in app.store.users() if u["name"] not in (user, "household")]
    for who in [user, "household"] + others:      # the speaker's, the household's, anyone's
        acct = app.store.user_settings(who).get(service) or {}
        if all(acct.get(k) for k in req) and (service != "jellyfin" or acct.get("api_key") or
                                              (acct.get("username") and acct.get("password"))):
            return dict(acct, _user=who)
    return None


def anyone_has(app, service):
    return any(account(app, u["name"], service) for u in app.store.users())


def public_view(settings):
    """User settings with the secrets masked, for the UI."""
    out = {}
    for svc, acct in (settings or {}).items():
        if isinstance(acct, dict):
            out[svc] = {k: ("********" if k in SECRET and v else v) for k, v in acct.items()}
    return out


def merge(old, new):
    """Applies UI changes, keeping secrets sent back masked."""
    out = dict(old or {})
    for svc, acct in (new or {}).items():
        if svc not in SERVICES or not isinstance(acct, dict):
            continue
        cur = dict(out.get(svc) or {})
        for k, v in acct.items():
            if k in SECRET and v == "********":
                continue
            cur[k] = (v or "").strip()
        out[svc] = cur
    return out

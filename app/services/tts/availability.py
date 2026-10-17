"""Gateway availability policy for inference moved to separate workers."""

LOCAL_KOKORO_UNAVAILABLE = "Local Kokoro is not available on this gateway."


def is_local_kokoro(provider, is_local):
    return str(provider).strip().lower() == "kokoro" and bool(is_local)

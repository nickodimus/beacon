"""beacon — the hub side of the visual message notifier.

One job: accept an authenticated POST from the phone (via Apache on
<api-host>), and publish one small JSON trigger to Mosquitto on
beacon/<device>/trigger. The device firmware (chunk 2) subscribes and
flashes. The service stores nothing and reads nothing back.

Shape copied from mailroom: static bearer, fails closed, per-process rate
ceiling, config out of source, token never logged.
"""

__version__ = "0.1.0"

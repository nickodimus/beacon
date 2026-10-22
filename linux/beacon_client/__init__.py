"""beacon Linux client: subscribe to the trigger topic, flash an LED.

Interim "device" on a Raspberry Pi (chunk 5) until the MicroPython board exists.
Stdlib only, plus gpiozero for the pin. Run from linux/:

    python3 -m beacon_client                 # the client; config from
                                             # ~/.config/beacon/device.toml
    python3 -m beacon_client --test-fire     # play the pattern once, no broker
"""

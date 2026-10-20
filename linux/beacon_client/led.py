"""The pin. gpiozero's LED on a BCM number; lgpio is the backend on pi."""


def open_led(gpio, active_high):
    try:
        from gpiozero import LED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is not installed (on Debian: apt install python3-gpiozero "
            "python3-lgpio)") from exc
    # active_high=False inverts for an LED wired pin -> resistor -> 3V3.
    # initial_value=False: dark at start, whichever way it is wired.
    return LED(gpio, active_high=active_high, initial_value=False)

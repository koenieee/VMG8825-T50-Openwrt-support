#!/usr/bin/env python3
"""Power-cycle helper for `dev_flash_cycle.py`.

The bootloader's autoboot window is only ~5 seconds, so the CR-spam that
catches `ZHAL>` has to already be running *before* power returns. Doing this
by hand (pull the plug, wait, plug back in, switch to the terminal) reliably
misses that window. This module gives `dev_flash_cycle.py` a single blocking
`power_cycle_and_catch_zhal()` call that starts the catch loop with no gap
after power-on.

Two backends:

- **manual** (default): prints instructions and waits for you to power-cycle
  the router by hand, then presses on immediately after you hit Enter. Works
  with any setup, no extra hardware required.
- **ha**: drives a Home Assistant-controlled smart plug over its REST API,
  fully unattended. Opt in with:
      export POWER_BACKEND=ha
      export HA_URL="http://homeassistant.local:8123"
      export HA_TOKEN="<long-lived access token from your HA profile page>"
      export HA_POWER_ENTITY="switch.your_router_power_outlet"
  If you have a different network-controllable smart plug, copy the `ha`
  backend below and swap in your own API calls -- the important part is
  that the whole off->wait->on sequence stays inside one blocking call in
  the same process that then immediately starts reading the serial port.

Usage as a library:
    from power_cycle import power_cycle_and_catch_zhal
    power_cycle_and_catch_zhal(send_cr_fn, off_seconds=15, catch_seconds=90)

Usage standalone:
    python3 power_cycle.py cycle [off_seconds]
"""
import json
import os
import sys
import time
import urllib.request

BACKEND = os.environ.get("POWER_BACKEND", "manual")

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HA_ENTITY_ID = os.environ.get("HA_POWER_ENTITY", "switch.router_power_outlet")


# --- manual backend --------------------------------------------------------

def _manual_power_cycle(off_seconds=15):
    input("[power_cycle] power OFF the router now, then press Enter: ")
    print("[power_cycle] waiting %ds before power-on" % off_seconds)
    time.sleep(off_seconds)
    input("[power_cycle] power ON the router now, then press Enter immediately: ")


# --- Home Assistant backend -------------------------------------------------

def _ha_call(service, entity_id=HA_ENTITY_ID, timeout=10):
    url = "%s/api/services/switch/%s" % (HA_URL, service)
    body = json.dumps({"entity_id": entity_id}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer %s" % HA_TOKEN,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _ha_get_state(entity_id=HA_ENTITY_ID, timeout=10):
    url = "%s/api/states/%s" % (HA_URL, entity_id)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % HA_TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ha_wait_for_state(want, entity_id=HA_ENTITY_ID, timeout=10, poll=0.5):
    end = time.time() + timeout
    while time.time() < end:
        if _ha_get_state(entity_id).get("state") == want:
            return True
        time.sleep(poll)
    return False


def _ha_power_cycle(off_seconds=15):
    if not HA_TOKEN:
        raise RuntimeError("POWER_BACKEND=ha but HA_TOKEN is not set")
    print("[power_cycle] (ha) turning OFF %s" % HA_ENTITY_ID)
    _ha_call("turn_off")
    if not _ha_wait_for_state("off"):
        raise RuntimeError("plug did not report 'off' within timeout")
    print("[power_cycle] (ha) off confirmed, waiting %ds" % off_seconds)
    time.sleep(off_seconds)
    print("[power_cycle] (ha) turning ON %s" % HA_ENTITY_ID)
    _ha_call("turn_on")
    if not _ha_wait_for_state("on"):
        raise RuntimeError("plug did not report 'on' within timeout")
    print("[power_cycle] (ha) on confirmed")


# --- public API --------------------------------------------------------

def power_cycle(off_seconds=15):
    if BACKEND == "ha":
        _ha_power_cycle(off_seconds=off_seconds)
    else:
        _manual_power_cycle(off_seconds=off_seconds)


def power_cycle_and_catch_zhal(catch_zhal_fn, off_seconds=15):
    """Runs the power cycle, then immediately invokes catch_zhal_fn() (a
    zero-arg callable that starts the CR-spam catch loop)."""
    power_cycle(off_seconds=off_seconds)
    return catch_zhal_fn()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "cycle":
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        power_cycle(off_seconds=secs)
    else:
        print(__doc__)
        sys.exit(1)

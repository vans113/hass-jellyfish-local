# Hardware check-out checklist

Everything here is tested against a fake controller that copies the behaviour of the jellyfishlights-py library and the upstream integration. These items depend on the real firmware and need checking once the controller is installed. Turn on debug logging first:

```yaml
logger:
  logs:
    custom_components.jellyfish_local: debug
```

Every message sent and received then shows up in the log. Download diagnostics from the device page too.

## Must confirm

- [ ] **Setup:** add by IP; the entry title is the controller's name and every zone shows up.
- [ ] **On/off per zone:** plain turn-on **resumes** the last pattern. The fake does this; if the real controller turns on to black or a default, we change `turn_on`.
- [ ] **Solid color and brightness:** color is correct, and the brightness slider matches the app.
- [ ] **Pattern at a brightness** (`effect` + `brightness_pct`): the pattern plays **and** is dimmed. We send `file` and the modified `data` together. If the controller ignores `data` when a `file` is given, brightness won't change. The fallback is to send `data` only.
- [ ] **Brightness on a running pattern:** same mechanism as above.
- [ ] **State after commands:** HA updates within a second or so. If it lags, check the log for "No state confirmation".
- [ ] **Push from the app:** change a zone in the JellyFish app and check HA updates.
- [ ] **Light string service:** a colors sequence renders along the zone.

## Reliability

- [ ] **Restart HA:** it reconnects on the first try (the websocket is closed cleanly on shutdown).
- [ ] **Reload the integration:** it reconnects with no "cannot connect" loop.
- [ ] **Power-cycle the controller:** entities go unavailable, then come back within about a minute.
- [ ] **Reconfigure with the same IP:** succeeds (the integration releases the single slot during the check).
- [ ] **HA and the phone app at the same time:** note what happens. Does the controller refuse the app, or kick HA? If it kicks HA, HA will steal the slot back within 60 s, and we may want a longer back-off.
- [ ] **Controller blocked from the internet:** everything above still works.

## Nice to find out

- [ ] Controller hostname and whether it advertises over mDNS/DHCP. If so, we can add auto-discovery.
- [ ] Does the controller answer websocket pings? If so, we can detect dead connections faster than the 60 s poll.
- [ ] Firmware version string and the `isUpdate` flag, to see whether a firmware-update sensor is worthwhile.

# Phone recipe — Signal notification → beacon trigger

The phone does one thing: when Signal posts a notification, POST to the beacon endpoint.
No device, no MQTT, no token anywhere but the HTTP header. Endpoint contract (chunk 1):

    POST https://<api-host>/beacon/trigger
    Authorization: Bearer <api_token>          content-type: application/json
    {"source":"signal"}                        200 published · 401 bad token · 429 over 12/min

Signal's package name is `org.thoughtcrime.securesms`. Filter on the package, not the app
name, so a renamed or beta build still matches.

Checked against: MacroDroid wiki (wiki.macrodroid.com, pages dated Jul–Oct 2026, describes
the 6.0 UI; Play listing updated 2026-10-06, version shown as "varies with device") and
Tasker 6.6.20 (Play, 2026-02-24). Menu names below are from those sources, not memory.

## MacroDroid (first choice; free tier = 5 macros, enough)

One macro: Trigger → Action → two settings.

1. **Trigger**: *Notification* → Trigger Event **Notification Received** → **Select
   Applications** → tick Signal (Include). Text filter **Any text**. Tick **Ignore
   ongoing/persistent notifications** (Signal's "background connection" notice is one).
   Leave **Prevent multiple triggers** on (default). MacroDroid asks for Notification
   Access the first time; grant it.
2. **Action**: *HTTP Request*. **Request method** POST. **URL** the endpoint above.
   **Header Params**: one row, key `Authorization`, value `Bearer ` + the token (space
   after Bearer). **Content Body**: content type `application/json`, text
   `{"source":"signal"}`. Optional but worth it: **Save HTTP return code in integer
   variable** `beacon_code`, then an *If* `beacon_code != 200` → *Display Notification*
   "beacon {v=beacon_code}". That turns 401/429/no-WiFi into something you can see.
3. **Keep it alive**: MacroDroid Settings, top entry **Ignore Battery Optimisations** →
   accept the system prompt. Also clear the home-screen warning bar and the
   *Troubleshooting → Issues Identified* tab. Samsung/Xiaomi/Oppo add their own killers;
   follow https://dontkillmyapp.com for the brand.
4. **Wi-Fi constraint (optional)**: *Wifi* → *Connected to network* → home SSID. Skips
   the pointless 4G attempt; the beacon is LAN-only anyway.

## Tasker (alternative, paid)

- **Profile**: Event → UI → **Notification**. **Owner Application** Signal. Leave Title
  and text fields empty. Tick **New Only** so a notification Signal re-posts (unread count
  update) does not fire again. Grant Tasker Notification Access when prompted.
- **Task**: Net → **HTTP Request**. Method POST. URL the endpoint. **Headers**, one per
  line, no space round the colon:
  `Authorization:Bearer <api_token>` and `Content-Type:application/json`.
  **Body** `{"source":"signal"}`. **Timeout** 10. Result lands in `%http_response_code`
  (and `%http_data`); add a *Flash* of `%http_response_code` while testing.
- Battery: Android Settings → Apps → Tasker → Battery → **Unrestricted**.

## Testing from the phone

1. Request path first, no notification needed. MacroDroid: macro editor menu → **Test
   actions**. Tasker: the play button in the task editor. Expect 200 and, if the device or
   `tools/watch.py` on workstation is subscribed, one flash / one line.
2. Trigger path with a dummy notification. MacroDroid: temporarily switch the trigger to
   **Any Application**, start a 10-second timer in the Clock app, confirm the fire, switch
   back to Signal. Tasker: clear **Owner Application** for the same test. Tasker never
   fires on its own notifications, so use another app's, not a self-posted one.
3. Real path: have someone send a Signal message. Note to Self does not notify the sender.

## "Hide message content"

Signal → profile → Notifications → **Show**: *Name and message* / *Name only* / *No name or
message*. Any of the three still posts a notification; the trigger keys on the
notification's existence, not its text, so **Any text** keeps working with content hidden.
Android's lock-screen "hide content" is a display rule and does not affect the listener.
Nothing from the message (sender, body) is sent anywhere; the body is a constant.

## Token

The token lives in the automation app's HTTP header field and nowhere else on the phone:
not in a variable, not in the macro name, not in a backup you share. MacroDroid exports
include it in clear, so do not upload the macro to the Template Store. The same value
sits in `~/.config/beacon/config.toml` on hub and `~/.config/beacon/api.token` on
workstation; if it rotates, update the phone header by hand.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| 401 | Header is `Authorization: Bearer <token>`, exactly one space, no quotes, token not stale. 503 = hub's config has an empty token. |
| 429 | More than 12 triggers in a minute (`max_per_min`). A Signal group burst can do it; the next minute clears. Not a phone fault. |
| 422 | Body is not `{"source":"signal"}` or content type is not JSON. |
| No request logged | No Wi-Fi: the endpoint is public HTTPS, so mobile data also works, but a Wi-Fi constraint (step 4) silently skips. Check the MacroDroid System Log / Tasker Run Log. |
| Fires once, then never | Battery saver or OEM killer. Redo the battery step; check Troubleshooting → Issues Identified; dontkillmyapp.com. |
| Nothing fires at all | Notification Access revoked (Android auto-revokes unused-app permissions). Re-grant. On Android 15+ "Enhanced notifications" can withhold some content, but the fire still happens. |
| 200 but no flash | Phone side is done. Look at the broker/device: `tools/watch.py` on workstation, chunk 1 README. |

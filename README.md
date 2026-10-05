# MaNoUmi

<img src="custom_components/manoumi/brand/icon.png" alt="MaNoUmi icon: a dragon in a triangle" width="128" align="right">

**Room-level presence for Home Assistant, from the Bluetooth proxies you already have.**

Your phone talks Bluetooth all the time. If your home is full of Bluetooth proxies (Shelly relays, ESPHome boards), each of them hears it a little differently. MaNoUmi learns how that pattern looks in each of your rooms and from then on tells you which room your phone is in. You calibrate each room once, about a minute per room, and after that MaNoUmi keeps learning on its own while you use it.

The name is a nod to [Bermuda](https://github.com/agittins/bermuda), the integration that inspired this one. *Ma-no Umi*, the "Devil's Sea", is the Pacific's dragon triangle, Bermuda's counterpart on the other side of the globe.

## Why you'd want this

Most Bluetooth room-presence setups pick the scanner that hears you loudest. That falls apart quickly in a real house:

- **Proxies sit where the wiring is, not where you are.** Light switches live next to doors. Two Shellys behind the switches on either side of the same wall are almost the same distance from you. A "nearest scanner wins" approach then flips back and forth between the two rooms every few seconds.
- **Signal strength is noisy.** A phone in your pocket, in your hand or behind your back easily differs by 10 dB or more, without you moving at all.

MaNoUmi doesn't ask "who is closest?". It asks "which room does this look like?" and uses *all* scanners for the answer. The scanners on the far side of the house are often the ones that tell two neighbouring rooms apart.

- **No floor plan, no coordinates, no distances to measure.** You just stand in a room and tell MaNoUmi which room it is.
- **Rooms without their own scanner work too.** A room is recognisable as soon as it has its own pattern.
- **Pocket-proof.** If every scanner suddenly hears you a few dB weaker, MaNoUmi treats that as "phone in a pocket", not as "somewhere else".
- **It keeps learning.** Whenever MaNoUmi is sure where you are, it quietly refines that room. Your calibration always keeps the upper hand, though: learned data can never override it.
- **It tells you where it needs help.** After each calibration you get a short report, and a status page shows which rooms are fine and which could use another round.

## What you need

- Home Assistant 2026.9 or newer.
- Bluetooth proxies spread around your home. Any scanner Home Assistant knows works: Shelly Gen2/Gen3 devices, ESPHome Bluetooth proxies, or a USB adapter. For Shellys, set *Bluetooth scanner mode* to **passive** in the Shelly integration's options. More scanners in more places help, but you don't need one per room.
- The device you want to track, set up with Home Assistant's [Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/) integration. This is how iPhones, Apple Watches and Android phones with rotating Bluetooth addresses are recognised (via their IRK).

## Installing it

Through [HACS](https://hacs.xyz/):

1. Open HACS, click the three dots in the top right corner and choose "Custom repositories".
2. Paste this page's address, pick "Integration" as the category and confirm.
3. Find "MaNoUmi" in HACS, install it, and restart Home Assistant.

By hand: copy the folder `custom_components/manoumi` into the `custom_components` folder of your Home Assistant configuration and restart Home Assistant.

## Setting it up

Go to **Settings → Devices & Services → Add Integration** and search for "MaNoUmi". You'll be asked what you want to set up.

**Track a device** (once per phone or watch):

1. Give it a name, e.g. the person's name.
2. Pick the Private BLE Device tracker of that phone.
3. Choose the rooms. Rooms that contain a Bluetooth scanner are preselected, and you can add others.
4. Optionally pick a notify service (e.g. `notify.mobile_app_…`). Calibration messages then also arrive as push notifications on your phone, which is handy because that's the device you're walking around with.

**Home** (optional, once per installation):

- Counts how many tracked people are in each room.
- Uses **reference devices** to keep your calibration valid. These are Bluetooth devices that never move, such as thermometers or hubs. If the scanners suddenly hear all of them weaker or stronger (new firmware, a replaced or moved proxy), MaNoUmi removes that shift from every measurement.

## Calibrating

For each room:

1. Go to the room with the phone.
2. On the device page, choose the room under **Calibrate**.
3. Move around normally for up to a minute: walk to the spots where you usually are, and have the phone sometimes in your hand and sometimes in your pocket.

The calibration stops by itself as soon as the room is reliably recognisable, often after less than a minute. You get a message with the result. It tells you how reliably the room is recognised, which room it is most easily confused with, and whether a room calibrated earlier has become harder to tell apart because of the new one.

Calibrate every room once first. Then open **MaNoUmi → Configure**. The status page lists each room as ✅ done, ⚠️ needs another round, or ⬜ not calibrated. For a ⚠️ room, set **Calibration mode** to **Add** and calibrate it again from a different spot in the room. Large rooms and hallways typically benefit from two positions.

**New** replaces a room's calibration, for example after moving furniture around. **Add** keeps the existing calibration and adds another position. **Reset learned data** throws away what MaNoUmi learned by itself and keeps your calibration.

## What you get

Per tracked device (attached to its Private BLE Device):

| Entity | What it tells you |
|---|---|
| **Room** | The current room, or *Nicht da* (away) when no scanner has heard the device for a minute. Attributes include the confidence, a short explanation of the decision ("Office 82 % ahead of Hallway 12 % – decisive: Shelly Kitchen (−84 dBm)"), the last known room, when the device was last seen, and the calibration state of every room. |
| **Floor** | The floor of the current room, taken from your area/floor setup. |
| **Calibrate** / **Calibration mode** | Start a calibration and choose between New and Add. |
| **Reset learned data** | Go back to the plain calibration. |

For the Home entry:

| Entity | What it tells you |
|---|---|
| **People ‹room›** | How many tracked devices are in that room right now, and which ones. |
| **Scanner drift** | How far the scanners have drifted from the baseline recorded with your reference devices. |
| **Re-capture baseline** | Use after moving a reference device. |

There is also an action, `manoumi.calibrate` (room, mode, duration), so you can start a calibration from a dashboard button or a script.

## Good to know

- **Everything stays local.** MaNoUmi only reads what your Bluetooth scanners already report to Home Assistant.
- **Recording measurements** (Home entry → Configure) writes all measurement windows to `/config/manoumi/capture.jsonl`. This is useful if you want to dig into the numbers or report a problem. It is off by default, and the file never leaves your Home Assistant.
- **Several people:** add one device entry per phone. The *People ‹room›* sensors in the Home entry add them up.
- **Language:** the setup dialogs, entity names and notifications are available in English and German. The room sensor's attribute names, the explanation text and the status page are currently German only.
- **Diagnostics** (device page → Download diagnostics) contains the learned model and the last measurement windows. Attach it if you open an issue.

## How it works

For the curious, here is what happens under the hood:

- Every two seconds, MaNoUmi reads, for every scanner, the last advertisement of the tracked device and its timestamp. Every six seconds these are condensed into a *window*: a robust mean RSSI per scanner, plus which scanners heard nothing at all.
- Each room position stores, per scanner, a mean, a spread and how often that scanner hears the device there. A window is scored against every room with a naive-Bayes likelihood.
- A common level offset across all scanners is estimated for each window and removed. Whether a weak scanner is *expected* to hear the device follows that offset.
- A small hidden-Markov step with some stickiness turns window scores into the room shown. Single outlier windows don't flip the room.
- Self-learning only kicks in after about two minutes of confident, stable detection. Learned data is capped at 30 % of the model.
- Calibration quality is checked by leave-one-out: each calibration window is tested against a model that has never seen it.

## Credits

Ideas from [Bermuda](https://github.com/agittins/bermuda) (polling scanners for per-advert RSSI, deduplicating adverts by timestamp, Private BLE handling, linking entities to the existing device) and from [ESPresense](https://github.com/ESPresense/ESPresense) (the trimmed-mean RSSI filter, and using fixed devices as references for drift). No code was copied.

## Contributing

Issues and pull requests are welcome.

```bash
python -m pytest tests/ -q                       # run the test suite
python -m ruff check custom_components tests     # lint
python tools/analyze.py capture.jsonl            # evaluate a recording offline
```

Licensed under the [MIT License](LICENSE).

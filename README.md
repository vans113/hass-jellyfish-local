# JellyFish Local

Local-only Home Assistant integration for [JellyFish Lighting](https://jellyfishlighting.com) permanent outdoor lights. It talks straight to the controller on your network, with no cloud. It keeps working with the controller blocked from the internet.

Unofficial; not made or endorsed by JellyFish Lighting.

## What you get

For each controller:

| Entity | What it does |
| --- | --- |
| `light.<controller>_<zone>` | One light per zone: on/off, solid RGB color, brightness, and every saved pattern as an effect |
| `light.<controller>_all_zones` | Every zone at once (only created when there's more than one zone) |
| `select.<controller>_<zone>_pattern` | Pattern picker per zone, handy for dashboards |
| `binary_sensor.<controller>_connectivity` | Whether Home Assistant is connected to the controller (diagnostic) |
| `button.<controller>_refresh_patterns_and_zones` | Pick up patterns or zones you just made in the JellyFish app (diagnostic) |

Behaviour worth knowing:

- **Changing brightness keeps what's showing.** A running pattern stays that pattern, just dimmer. So do solid colors and custom light strings.
- **Turning a zone on without options resumes what it showed last.**
- **Live updates.** Changes made in the JellyFish app or by the controller's own schedule appear in Home Assistant right away. A light poll every 60 s only checks that the connection is alive.
- **Zones added or removed on the controller** are picked up automatically, within 30 min or immediately with the refresh button.

### The JellyFish card

The integration ships its own dashboard card, with nothing extra to install. Add a card of type **JellyFish** (or `custom:jellyfish-card` in YAML):

```yaml
type: custom:jellyfish-card
entity: light.jellyfish
# optional
entities: [light.jellyfish_all_zones, light.jellyfish_front, light.jellyfish_side]  # zone picker
folders: [Accent, Colors, Halloween, Christmas, Home Assistant]                     # limit the list
save_folder: Home Assistant
```

- **Looks**: every saved pattern by folder, each with a preview of its colors. Tap to play.
- **Make your own**: pick colors, choose how they move (Solid, Chase, Paint, Stacker, Sequence, Multi-Paint), plus speed, direction and gap. **Show it on the house** runs it. **Save as a look** stores it on the controller under a name, so it shows up in Home Assistant and in the JellyFish app.
- A live roofline at the top shows the colors that are on.

### Custom patterns and live tweaks

Each zone gets **effect type** and **direction** selects, plus **speed** (1 = slowest, 100 = fastest) and **spacing** numbers. They change whatever is playing, like the app's edit screen.

```yaml
action: jellyfish_local.run_pattern       # build a look in an automation or script
target: {entity_id: light.jellyfish}
data:
  colors: [[255, 100, 0], [128, 0, 128]]
  pattern_type: Chase
  speed: 60
  direction: Left
  brightness_pct: 70
```

`jellyfish_local.adjust_pattern` changes parts of what's running (any of the same fields). `jellyfish_local.save_pattern` (`name`, optional `folder`) saves what's running as a named pattern.

Looks made in the JellyFish app without saving show up as **"Custom: Warm White + Red Orange"**, named after their colors.

### Service: `jellyfish_local.apply_light_string`

Paints a repeating sequence of colors along one or more zones.

```yaml
action: jellyfish_local.apply_light_string
target:
  entity_id: light.front_house_front
data:
  colors: [[255, 0, 0], [255, 255, 255], [0, 128, 0]]
  brightness_pct: 80
```

### Examples

```yaml
# Play a saved pattern at 40 %
action: light.turn_on
target:
  entity_id: light.front_house_all_zones
data:
  effect: "Christmas/Candy Cane"
  brightness_pct: 40
```

Pattern names are `Folder/Name`, exactly as they appear in the JellyFish app.

## Installing

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/vans113/hass-jellyfish-local`, category **Integration**.
2. Download **JellyFish Local**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → JellyFish Local**, and enter the controller's IP address.

### Manual

Copy `custom_components/jellyfish_local` into your Home Assistant `config/custom_components/` folder and restart.

## Tips

- **Give the controller a fixed IP address** with a DHCP reservation on your router. If it ever changes, use **Reconfigure** on the integration, not delete and re-add.
- **The controller allows only one connection at a time.** If setup says it can't connect, close the JellyFish app on your phone and try again. While Home Assistant is connected, the app may not be able to connect.
- **Blocking the controller from the internet** is fine. Note that the controller's built-in schedule still runs unless you clear it in the app. If Home Assistant handles scheduling, clear it there.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest          # runs against a fake controller in tests/fake_controller.py
ruff check . && ruff format --check .
```

## Credits

Protocol details come from [jellyfishlights-py](https://github.com/vinenoobjelly/jellyfishlights-py) by @vinenoobjelly. The original integration, [hass-jellyfish-lighting](https://github.com/bdunn44/hass-jellyfish-lighting), is by @bdunn44. This integration is a from-scratch rewrite with its own async client.

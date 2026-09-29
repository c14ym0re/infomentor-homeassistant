# InfoMentor for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![hassfest](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/hassfest.yml/badge.svg)](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/hassfest.yml)
[![Tests](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/c14ym0re/infomentor-homeassistant/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A first-class Home Assistant integration for **InfoMentor** (the Swedish school
platform, "Mentor" / "InfoMentor Hub"). It signs in with your normal
email/password account and turns your children's school day into sensors you can
build automations on.

> ⚠️ **Unofficial and not affiliated with InfoMentor.** For personal use with
> your own account. The API is undocumented and can change without warning —
> use at your own risk and respect the service's terms.

## What you get

One device per child, plus a hub device:

| Entity | Type | Meaning |
|---|---|---|
| `sensor.<child>_school_day` | sensor | school day start–end (next school day, e.g. `08:00–14:40`), with the first/last lesson as attributes |
| `sensor.<child>_assignments_due` | sensor | number of assignments due within 7 days, with the list as an attribute |
| `sensor.<child>_next_event` | sensor | next calendar event (test, trip, …) |
| `sensor.<child>_weekly_letter` | sensor | the teacher's **weekly letter** (learnlog): latest title, with full text, subject, date and attachments as attributes |
| `sensor.<child>_plans` | sensor | number of **plans** (Unit of Learning) in progress, with the open ones — title, subject, period, teacher, state and their `assignments` (the tests and homework linked to the plan) — as attributes |
| `binary_sensor.<child>_pe_next_school_day` | binary sensor | on when PE/gymnastics is coming up — *remember the gym bag* |
| `sensor.infomentor_school_news` | sensor | school news: count, with the latest 10 as attributes |
| `sensor.infomentor_school_lunch` | sensor | school lunch (optional, from Mateo) |

> Entity IDs follow your Home Assistant language: the suffixes above are the
> English names. A Swedish HA gives `…_skoldag`, `…_uppgifter`,
> `…_nasta_handelse`, `…_veckobrev`, `…_planeringar`,
> `…_idrott_nasta_skoldag`. Check yours under
> Settings → Devices & Services → InfoMentor → device → Entities.

Everything is polled on a configurable interval (default 20 minutes) using a
`DataUpdateCoordinator`, with **re-authentication** handled by Home Assistant.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/c14ym0re/infomentor-homeassistant` as **Integration**
3. Install **InfoMentor** and restart Home Assistant
4. **Settings → Devices & Services → Add Integration → InfoMentor**

### Manually

Copy `custom_components/infomentor` into your HA `config/custom_components/`
folder and restart.

## Configuration

Enter the **email and password** of your InfoMentor (Mentor) account. The
integration signs in, discovers every child on the account, and creates the
entities above.

> Some municipalities use BankID/SSO instead of the email/password account. This
> integration uses the email/password flow; it depends on your school.

### Options

| Option | Default | Description |
|---|---|---|
| Update interval | 20 min | How often to poll (5–180). |
| Fetch school lunch | off | Download the menu from Mateo. |
| Mateo unit id | – | Numeric id from `meny.mateo.se/<municipality>/<id>`. |
| Nicknames | – | One per line: `Lastname, Firstname = Nickname`. |

## Automation ideas

🇸🇪 **Svensk guide med fler exempel (idrottspåminnelse, morgonöversikt,
skolpanel):** [examples/sa-funkar-det.md](examples/sa-funkar-det.md) —
ready-made dashboards: [built-in cards](examples/skolpanel.yaml) /
[Mushroom](examples/skolpanel-mushroom.yaml)

```yaml
# Remind about the gym bag the evening before
automation:
  - alias: "Påminnelse idrott"
    triggers:
      - trigger: time
        at: "19:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.anna_pe_next_school_day
        state: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Idrott imorgon – packa idrottskläderna!"

# Show the school day on a dashboard (or import examples/skolpanel.yaml)
type: entities
entities:
  - sensor.anna_school_day
  - sensor.anna_assignments_due
  - sensor.anna_next_event
```

## How it works

The integration talks directly to `hub.infomentor.se` using `aiohttp` (bundled
with Home Assistant — no extra dependencies). Login follows the documented
email/password flow (`oauth_token` → mentor login form → credentials →
`isauthenticated`) and the session lives in cookies. Each poll logs in again, so
an expired session never matters.

All the endpoints were mapped and documented by the companion project
**[infomentor-api](https://github.com/c14ym0re/infomentor-api)** (CLI, email
reports, event alerts, MQTT bridge) — this integration is the Home Assistant
face of it.

## Troubleshooting

- **"Invalid email or password"** – check the credentials at
  <https://infomentor.se/swedish/production/mentor/>; some municipalities use SSO.
- **Re-authentication prompt** – the session was rejected; Home Assistant asks
  for the password again (nothing is logged out permanently).
- **No entities** – make sure the account actually has children with an active
  placement.

## Feedback & bug reports

This is a fresh **beta** integration, and bug reports directly shape the next
release — please report anything odd, even small things.

- 🐛 **[Open a bug report](https://github.com/c14ym0re/infomentor-homeassistant/issues/new?template=bug_report.yml)**
  — the form asks for exactly what we need (and takes a minute).
- ✨ **[Suggest an idea](https://github.com/c14ym0re/infomentor-homeassistant/issues/new?template=feature_request.yml)**
- 📖 **[Swedish guide with examples and dashboards](examples/sa-funkar-det.md)**

**What helps most:**
1. **Diagnostics** — Settings → Devices & Services → InfoMentor → ⋮ →
   *Download diagnostics* (sensitive fields are already redacted). Attach the file.
2. **Log lines** mentioning `infomentor` — Settings → System → Logs.
3. **Which municipality** your school is in. Behaviour differs between them, and
   that is exactly the kind of surprise we want to find.

## Credits

- [kolplattformen/skolplattformen](https://github.com/kolplattformen/skolplattformen)
  (Apache-2.0) — endpoint documentation.
- [kolplattformen/dementor.net](https://github.com/kolplattformen/dementor.net)
  (MIT) — login flow.
- [c14ym0re/infomentor-api](https://github.com/c14ym0re/infomentor-api) — the
  companion toolkit and reference implementation.

## License

MIT © 2026 Claes Hall — see [LICENSE](LICENSE).

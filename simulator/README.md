# nikobus-simulator

A simulated Nikobus installation. It listens on a TCP port and speaks the
protocol a PC-Link speaks, so `nikobus-connect` — and Home Assistant above
it — can be run, developed and tested without any hardware.

```bash
nikobus-simulator --preset house --port 9999
```

Then point the integration at `127.0.0.1:9999`.

## Describing an installation

The input is a declaration of what the installation contains, so you can
simulate modules you do not own:

```yaml
gateway: { address: "86F5", family: pc_link }
modules:
  - address: "4707"
    type: switch_module
    channels: 12
    links:
      - { button: "0B1380", key: 0, channel: 1, mode: 0 }
      - { button: "0B13A0", key: 2, channel: 7, mode: 4 }
  - address: "8334"
    type: audio_module
    zones: 4
```

From that the simulator **builds the memory a real module would hold**, so a
register read returns a genuine link table and discovery produces the
entities it would produce on the bus.

Two fields are worth spelling out:

* `button` is the **bus address** — the `#N` payload, the address a host
  presses and the one the integration shows. A module does not store it
  in that form; the encoder shuffles the bits the way the library's
  `get_button_address` shuffles them back.
* `mode` is the **raw** mode a record stores, one less than the vendor's
  mode number: `0` is M01 (on / off), `4` is M05 (impulse).

Other sources of a topology, all optional: a `.nkb` project file (it carries
a whole installation), a folder written by the integration's *Backup module
programming* (byte-exact replay of somebody's real module), and the presets
shipped here (`house`, `twelve`, `audio`).

## What it does, and does not

It reproduces the behaviours that are easy to get wrong and impossible to
see in a unit test:

* an **acknowledgement is withheld** until the gateway has something else
  to send, so the handshake's own query is answered only in front of the
  next frame — the quirk that made four releases warn about a bus that was
  never silent;
* the gateway **never relays the host's own press**, so a host learns the
  result of its own press only by asking;
* a **held key repeats on the bus**, and a module counts the burst as one
  press — which is what lets a host send a press several times over for
  reliability without an impulse link reading it as "on, then off again";
* a **state answer names no group**: the query that preceded it is the
  only thing that says which six channels came back;
* a twelve-channel module needs **both** group frames;
* a press is applied through the module's **own link table**, so an
  impulse link toggles and a roller link cycles open – stop – close;
* a **dimmer** answers its own read function (`0x22`) with eight bytes
  where every other family answers `0x10` with sixteen.

It is **not a model of Nikobus hardware**. It replays memory formats we have
decoded and applies rules we have documented. Where that understanding is
wrong, the simulator is confidently wrong in the same way. That is fine for
regression testing and misleading for learning how a module behaves.

Modules whose storage nobody has captured are stubs: present on the bus,
answering status, with no link table to read.

## Tests

```bash
cd simulator && pytest
```

The encoders are checked by handing their output to the library's own
decoders — a link declared here has to come back out of the decoder the
integration runs against real hardware — and the audio table is compared
byte for byte against a capture of a real 05-205. The end-to-end tests run
the shipped `NikobusConnect`, listener, command queue and discovery scan
against the simulator over a socket, with nothing mocked.

They need a checkout of `nikobus-connect` next to this directory, which is
what the branch gives them; `pythonpath` in `pyproject.toml` points at it.

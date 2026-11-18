# Contributing to ODIN

Thank you for helping improve ODIN. Contributions should preserve its core goal: a private, offline, Ubuntu voice assistant that runs on modest CPU hardware.

## Before you start

- ODIN is designed for Ubuntu 26.04 with GNOME/Wayland and PipeWire.
- Keep runtime functionality offline. Do not add cloud APIs, accounts, or network dependencies.
- Keep microphone audio in memory and avoid recording or transmitting voice data.
- Check existing issues before starting substantial work, and keep pull requests focused.

## Development setup

ODIN requires Linux and the system dependencies installed by the setup scripts:

```bash
sudo scripts/setup-system.sh
scripts/setup.sh
./odin.sh check
```

`setup-system.sh` needs root; `setup.sh` is for downloading the Python environment and voice models. After setup, the project virtual environment is available at `.venv/`.

## Making changes

- Put command phrases and intent mappings in `odin/intents.py`.
- Put configurable app aliases in `config.toml`.
- Keep actions isolated in the relevant module under `odin/actions/`.
- Use the existing CLI and configuration patterns rather than introducing another interface.
- Add or update tests for behavioral changes.
- Run `./odin.sh check` for configuration, models, and vocabulary validation.

When adding a spoken phrase:

1. Add or update it in the appropriate intent configuration or code.
2. Confirm it is present in the Vosk lexicon with `./odin.sh check`.
3. Run the unit and self-tests below.

## Testing

Run the fast, non-microphone test suite:

```bash
.venv/bin/python -I -m unittest discover -s tests
```

Run recognition validation without a microphone:

```bash
.venv/bin/python -I scripts/selftest.py
```

For an end-to-end hardware test, use:

```bash
.venv/bin/python -I scripts/airtest.py
```

The air test uses speakers and a microphone. It deliberately refuses destructive actions, but only run it in a private space because it performs local voice recognition.

## Pull requests

Please include:

- A clear description of the problem and the change.
- The Ubuntu, GNOME, PipeWire, and Python versions used for testing.
- Relevant test output.
- Screenshots or recordings only when they help explain a UI or hardware interaction.

Small, focused contributions are easier to review. Breaking changes to configuration or the wake/command recognition behavior should include migration guidance.

## Licensing

By contributing, you agree that your contributions may be distributed under the [MIT License](LICENSE).

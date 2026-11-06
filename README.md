# aicadcam

Archive and reconstruction tools for bundled project exports:

- **`aicam/`** — AICAM bundle (`aicam.txt`), splits, and `unbundled/` extracted app
- **`agenkadk/`** — Agent ADK bundle (`agent.txt`), splits, and `unbundled/` projects
- **`email&vad/`** — `emailvad.txt` bundle (email service + VAD automation), splits, `unbundled/`
- **`reconstruct_sqleditorapi.py`** — earlier sqleditor API chunk reassembly helper

Each subfolder includes Python scripts to reconstruct from JSON splits and unbundle where applicable.

## Reconstruct

```bash
# AICAM
cd aicam && python reconstruct_aicam.py && python unbundle_aicam.py

# Agent ADK
cd agenkadk && python reconstruct_agent.py && python unbundle_agent.py

# Email & VAD
cd "email&vad" && python reconstruct_emailvad.py && python unbundle_emailvad.py
```

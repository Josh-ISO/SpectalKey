# SpectralKey

<img width="1080" height="1080" alt="SpectralKey logo" src="https://github.com/user-attachments/assets/06f5d253-9712-4df7-81c6-bea0249d354d" />
SpectralKey is SDR Exploration project. It is currently in development! 

## Launch the prototype

On Windows, double-click **Launch SpectralKey.cmd** in this folder, then open http://127.0.0.1:8765 in your browser. Keep the terminal open while using the app; press Ctrl+C to stop it.

The launcher uses an installed Python or the local Codex Python runtime when available. Python 3.10 or newer is required; no third-party packages are needed.

Alternatively, from this repository folder:

```powershell
python "SpectralKey/Prototypes/prototype 1/signal_observatory.py"
```

Use **Load demo observations** to explore sample signals, or **+ Log signal** to add observations. This prototype logs and reviews HF signals; it does not connect to SDR hardware. Observations and recordings stay local and are excluded from Git.
